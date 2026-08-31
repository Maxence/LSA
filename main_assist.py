from __future__ import annotations

import queue
import secrets
import string
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk
from typing import Any, Callable

from assist_common import (
    ACTION_ATTACK,
    ACTION_FOLLOW,
    ACTION_IDS,
    APP_DIR,
    ConfigError,
    ForegroundInfo,
    KeyChord,
    action_label,
    chord_signature,
    format_clock,
    get_foreground_info,
    is_chord_down,
    is_running_as_admin,
    load_json_config,
    local_ipv4_addresses,
    resolve_key_spec,
    save_json_config,
    validate_pairing_key,
    validate_port,
)
from assist_network import MainAssistServer
from ui_utils import add_labeled_entry, append_log, apply_dark_style, make_log_widget


APP_VERSION = "2.0"
CONFIG_PATH = APP_DIR / "main_settings.json"


def generate_pairing_key() -> str:
    alphabet = string.ascii_uppercase + string.digits
    raw = "".join(secrets.choice(alphabet) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


MAIN_DEFAULTS: dict[str, Any] = {
    "main_name": "Main",
    "listen_host": "0.0.0.0",
    "port": 45880,
    "discovery_port": 45881,
    "pairing_key": "",
    "attack_trigger_key": "F2",
    "follow_trigger_key": "F3",
    "target_process": "L2.exe",
    "require_target_foreground": True,
    "poll_interval_ms": 10,
}


@dataclass(frozen=True)
class HotkeyBinding:
    action: str
    trigger_key: str
    chord: KeyChord


class HotkeyWatcher:
    def __init__(
        self,
        *,
        action_triggers: dict[str, str],
        target_process: str,
        require_target_foreground: bool,
        poll_interval_ms: int,
        on_trigger: Callable[[str, ForegroundInfo], None],
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        foreground = get_foreground_info()
        self.bindings = tuple(
            HotkeyBinding(
                action=action,
                trigger_key=action_triggers[action],
                chord=resolve_key_spec(action_triggers[action], foreground.keyboard_layout),
            )
            for action in ACTION_IDS
        )
        self.target_process = target_process
        self.require_target_foreground = require_target_foreground
        self.poll_interval = max(0.005, min(poll_interval_ms / 1000.0, 0.1))
        self.on_trigger = on_trigger
        self.on_event = on_event
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="MainAssist-Hotkeys", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread is not threading.current_thread() and thread.is_alive():
            thread.join(timeout=1.0)
        if thread and not thread.is_alive():
            self._thread = None

    def _emit(self, event_type: str, **payload: Any) -> None:
        if not self.on_event:
            return
        try:
            self.on_event({"type": event_type, "time": time.time(), **payload})
        except Exception:
            pass

    def _run(self) -> None:
        # Require a fresh press after startup/reconfiguration. A key that was
        # already held while the watcher started must not emit an action.
        was_down = {
            binding.action: is_chord_down(binding.chord, exact_modifiers=True)
            for binding in self.bindings
        }
        for binding in self.bindings:
            self._emit(
                "hotkey_started",
                action=binding.action,
                key=binding.trigger_key,
                chord=binding.chord.display(),
            )

        while not self._stop_event.is_set():
            current_down = {
                binding.action: is_chord_down(binding.chord, exact_modifiers=True)
                for binding in self.bindings
            }
            pressed = [
                binding
                for binding in self.bindings
                if current_down[binding.action] and not was_down[binding.action]
            ]

            if pressed:
                foreground = get_foreground_info()
                allowed = not self.require_target_foreground or foreground.matches(self.target_process)
                for binding in pressed:
                    if allowed:
                        self._emit(
                            "hotkey_pressed",
                            action=binding.action,
                            key=binding.trigger_key,
                            process=foreground.process_name,
                        )
                        try:
                            self.on_trigger(binding.action, foreground)
                        except Exception as exc:
                            self._emit(
                                "hotkey_error",
                                action=binding.action,
                                key=binding.trigger_key,
                                message=str(exc),
                            )
                    else:
                        self._emit(
                            "hotkey_ignored",
                            action=binding.action,
                            key=binding.trigger_key,
                            process=foreground.process_name or "inconnu",
                            expected=self.target_process,
                        )

            was_down = current_down
            self._stop_event.wait(self.poll_interval)

        self._emit("hotkey_stopped")


class MainAssistApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"L2 Main Assist v{APP_VERSION}")
        self.root.geometry("1040x820")
        self.root.minsize(900, 700)
        apply_dark_style(root)

        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.service_generation = 0
        self.server: MainAssistServer | None = None
        self.hotkey_watcher: HotkeyWatcher | None = None
        self.config = self._load_config()

        self._build_variables()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._start_services)
        self.root.after(100, self._process_events)
        self.root.after(250, self._refresh_runtime_status)
        self.root.after(600, self._refresh_peer_tree)

    def _load_config(self) -> dict[str, Any]:
        try:
            config = load_json_config(CONFIG_PATH, MAIN_DEFAULTS)
        except ConfigError as exc:
            messagebox.showerror("Configuration", str(exc))
            config = dict(MAIN_DEFAULTS)

        changed = False
        legacy_marker = object()
        legacy_trigger = config.pop("trigger_key", legacy_marker)
        if legacy_trigger is not legacy_marker:
            if str(legacy_trigger or "").strip():
                config["attack_trigger_key"] = str(legacy_trigger)
            changed = True

        if not str(config.get("pairing_key", "")).strip():
            config["pairing_key"] = generate_pairing_key()
            changed = True

        if changed:
            try:
                save_json_config(CONFIG_PATH, config)
            except ConfigError:
                pass
        return config

    def _build_variables(self) -> None:
        self.main_name_var = tk.StringVar(value=str(self.config["main_name"]))
        self.attack_trigger_key_var = tk.StringVar(value=str(self.config["attack_trigger_key"]))
        self.follow_trigger_key_var = tk.StringVar(value=str(self.config["follow_trigger_key"]))
        self.target_process_var = tk.StringVar(value=str(self.config["target_process"]))
        self.port_var = tk.StringVar(value=str(self.config["port"]))
        self.discovery_port_var = tk.StringVar(value=str(self.config["discovery_port"]))
        self.pairing_key_var = tk.StringVar(value=str(self.config["pairing_key"]))
        self.require_focus_var = tk.BooleanVar(value=bool(self.config["require_target_foreground"]))

        self.server_status_var = tk.StringVar(value="Serveur: démarrage…")
        self.focus_status_var = tk.StringVar(value="Focus: détection…")
        self.box_count_var = tk.StringVar(value="Box connectées: 0")
        self.admin_status_var = tk.StringVar(
            value="Privilèges: administrateur" if is_running_as_admin() else "Privilèges: standard"
        )
        ips = local_ipv4_addresses()
        self.address_var = tk.StringVar(value=f"IP locale: {', '.join(ips) if ips else 'non détectée'}")

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, style="Root.TFrame", padding=14)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text=f"L2 Main Assist v{APP_VERSION}", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="Écoute deux actions lorsque Lineage 2 est au premier plan, puis les diffuse à toutes les Box.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 12))

        status = ttk.Frame(outer)
        status.pack(fill="x", pady=(0, 10))
        for column in range(4):
            status.columnconfigure(column, weight=1)
        self.server_status_label = ttk.Label(status, textvariable=self.server_status_var, style="Status.TLabel")
        self.server_status_label.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.focus_status_label = ttk.Label(status, textvariable=self.focus_status_var, style="Status.TLabel")
        self.focus_status_label.grid(row=0, column=1, sticky="ew", padx=5)
        self.box_count_label = ttk.Label(status, textvariable=self.box_count_var, style="Status.TLabel")
        self.box_count_label.grid(row=0, column=2, sticky="ew", padx=5)
        admin_style = "Good.Status.TLabel" if is_running_as_admin() else "Warn.Status.TLabel"
        ttk.Label(status, textvariable=self.admin_status_var, style=admin_style).grid(
            row=0, column=3, sticky="ew", padx=(5, 0)
        )

        settings = ttk.LabelFrame(outer, text="Configuration du Main", padding=10)
        settings.pack(fill="x", pady=(0, 10))
        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(3, weight=1)

        add_labeled_entry(settings, 0, "Nom du Main", self.main_name_var, column=0, width=24)
        add_labeled_entry(settings, 0, "Processus du jeu", self.target_process_var, column=2, width=18)
        add_labeled_entry(settings, 1, "Touche Main - Attaquer", self.attack_trigger_key_var, column=0, width=24)
        add_labeled_entry(settings, 1, "Touche Main - Suivre", self.follow_trigger_key_var, column=2, width=18)
        add_labeled_entry(settings, 2, "Port TCP", self.port_var, column=0, width=24)
        add_labeled_entry(settings, 2, "Port découverte UDP", self.discovery_port_var, column=2, width=18)
        add_labeled_entry(settings, 3, "Clé d'appairage", self.pairing_key_var, column=0, width=24)

        focus_check = ttk.Checkbutton(
            settings,
            text="Ne réagir que si le processus configuré est au premier plan",
            variable=self.require_focus_var,
        )
        focus_check.grid(row=4, column=0, columnspan=4, sticky="w", pady=(5, 2))

        ttk.Label(
            settings,
            text="Par défaut: F2 diffuse Attaquer, F3 diffuse Suivre. Les touches jouées sont choisies sur chaque Box.",
            style="Muted.TLabel",
        ).grid(row=5, column=0, columnspan=4, sticky="w", pady=(2, 0))

        button_row = ttk.Frame(settings)
        button_row.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        ttk.Button(
            button_row,
            text="Enregistrer et redémarrer",
            style="Accent.TButton",
            command=self._save_and_restart,
        ).pack(side="left")
        ttk.Button(
            button_row,
            text="Tester Attaquer",
            command=lambda: self._send_test(ACTION_ATTACK),
        ).pack(side="left", padx=(7, 0))
        ttk.Button(
            button_row,
            text="Tester Suivre",
            command=lambda: self._send_test(ACTION_FOLLOW),
        ).pack(side="left", padx=7)
        ttk.Button(button_row, text="Copier les infos de connexion", command=self._copy_connection_info).pack(
            side="left"
        )

        connection = ttk.Frame(outer)
        connection.pack(fill="x", pady=(0, 8))
        ttk.Label(connection, textvariable=self.address_var, style="Muted.TLabel").pack(side="left")
        ttk.Label(
            connection,
            text="Les ports ne doivent être autorisés que sur le réseau privé.",
            style="Muted.TLabel",
        ).pack(side="right")

        peers_frame = ttk.LabelFrame(outer, text="Box connectées", padding=8)
        peers_frame.pack(fill="both", expand=True, pady=(0, 10))
        peers_frame.rowconfigure(0, weight=1)
        peers_frame.columnconfigure(0, weight=1)

        columns = ("name", "ip", "attack", "follow", "connected", "result")
        self.peer_tree = ttk.Treeview(peers_frame, columns=columns, show="headings", height=5)
        self.peer_tree.heading("name", text="Nom")
        self.peer_tree.heading("ip", text="Adresse IP")
        self.peer_tree.heading("attack", text="Attaquer")
        self.peer_tree.heading("follow", text="Suivre")
        self.peer_tree.heading("connected", text="Connectée depuis")
        self.peer_tree.heading("result", text="Dernière action")
        self.peer_tree.column("name", width=145, anchor="w")
        self.peer_tree.column("ip", width=120, anchor="w")
        self.peer_tree.column("attack", width=80, anchor="center")
        self.peer_tree.column("follow", width=80, anchor="center")
        self.peer_tree.column("connected", width=110, anchor="center")
        self.peer_tree.column("result", width=390, anchor="w")
        peer_scroll = ttk.Scrollbar(peers_frame, orient="vertical", command=self.peer_tree.yview)
        self.peer_tree.configure(yscrollcommand=peer_scroll.set)
        self.peer_tree.grid(row=0, column=0, sticky="nsew")
        peer_scroll.grid(row=0, column=1, sticky="ns")

        logs = ttk.LabelFrame(outer, text="Activité", padding=8)
        logs.pack(fill="both")
        logs.columnconfigure(0, weight=1)
        self.log_widget, log_scroll = make_log_widget(logs, height=6)
        self.log_widget.grid(row=0, column=0, sticky="nsew")
        log_scroll.grid(row=0, column=1, sticky="ns")

    def _emit_generation_event(self, generation: int, event: dict[str, Any]) -> None:
        tagged = dict(event)
        tagged["_generation"] = generation
        self.events.put(tagged)

    def _collect_config(self) -> dict[str, Any]:
        attack_trigger = self.attack_trigger_key_var.get().strip()
        follow_trigger = self.follow_trigger_key_var.get().strip()
        foreground = get_foreground_info()
        attack_chord = resolve_key_spec(attack_trigger, foreground.keyboard_layout)
        follow_chord = resolve_key_spec(follow_trigger, foreground.keyboard_layout)
        if chord_signature(attack_chord) == chord_signature(follow_chord):
            raise ConfigError("Les touches Main Attaquer et Suivre doivent être différentes.")
        if attack_chord.vk_code == follow_chord.vk_code:
            raise ConfigError(
                "Les deux actions Main doivent utiliser des touches physiques de base différentes "
                "(par exemple F2 et F3, pas F2 et CTRL+F2)."
            )

        target_process = self.target_process_var.get().strip()
        if not target_process:
            raise ConfigError("Le processus du jeu ne peut pas être vide.")

        return {
            "main_name": self.main_name_var.get().strip() or "Main",
            "listen_host": "0.0.0.0",
            "port": validate_port(self.port_var.get()),
            "discovery_port": validate_port(self.discovery_port_var.get()),
            "pairing_key": validate_pairing_key(self.pairing_key_var.get()),
            "attack_trigger_key": attack_trigger,
            "follow_trigger_key": follow_trigger,
            "target_process": target_process,
            "require_target_foreground": bool(self.require_focus_var.get()),
            "poll_interval_ms": 10,
        }

    def _start_services(self) -> None:
        self._stop_services()
        self.service_generation += 1
        generation = self.service_generation
        callback = lambda event, gen=generation: self._emit_generation_event(gen, event)
        try:
            config = self._collect_config()
            self.config = config
            server = MainAssistServer(
                host=str(config["listen_host"]),
                port=int(config["port"]),
                discovery_port=int(config["discovery_port"]),
                pairing_key=str(config["pairing_key"]),
                main_name=str(config["main_name"]),
                event_callback=callback,
            )
            server.start()
            self.server = server

            watcher = HotkeyWatcher(
                action_triggers={
                    ACTION_ATTACK: str(config["attack_trigger_key"]),
                    ACTION_FOLLOW: str(config["follow_trigger_key"]),
                },
                target_process=str(config["target_process"]),
                require_target_foreground=bool(config["require_target_foreground"]),
                poll_interval_ms=int(config["poll_interval_ms"]),
                on_trigger=self._on_hotkey_trigger,
                on_event=callback,
            )
            watcher.start()
            self.hotkey_watcher = watcher
        except Exception as exc:
            self._stop_services()
            self.service_generation += 1
            self.server_status_var.set("Serveur: erreur")
            self.server_status_label.configure(style="Bad.Status.TLabel")
            self._log(f"Démarrage impossible: {exc}", "error")

    def _stop_services(self) -> None:
        if self.hotkey_watcher:
            self.hotkey_watcher.stop()
            self.hotkey_watcher = None
        if self.server:
            self.server.stop()
            self.server = None

    def _trigger_for_action(self, action: str) -> str:
        if action == ACTION_ATTACK:
            return str(self.config["attack_trigger_key"])
        if action == ACTION_FOLLOW:
            return str(self.config["follow_trigger_key"])
        return "?"

    def _on_hotkey_trigger(self, action: str, _foreground: ForegroundInfo) -> None:
        server = self.server
        if server and server.running:
            server.broadcast_action(
                action=action,
                trigger=self._trigger_for_action(action),
                source="hotkey",
            )

    def _save_and_restart(self) -> None:
        try:
            config = self._collect_config()
            save_json_config(CONFIG_PATH, config)
        except Exception as exc:
            messagebox.showerror("Configuration invalide", str(exc))
            return
        self.config = config
        self._log("Configuration enregistrée. Redémarrage des services…", "info")
        self._start_services()

    def _send_test(self, action: str) -> None:
        server = self.server
        if not server or not server.running:
            self._log("Le serveur n'est pas démarré.", "error")
            return

        generation = self.service_generation

        def worker() -> None:
            try:
                server.broadcast_action(action=action, trigger="TEST", source="button")
            except Exception as exc:
                self._emit_generation_event(
                    generation,
                    {"type": "manual_broadcast_error", "time": time.time(), "message": str(exc)},
                )

        threading.Thread(target=worker, name=f"MainAssist-Test-{action}", daemon=True).start()

    def _copy_connection_info(self) -> None:
        ips = local_ipv4_addresses()
        ip_text = ips[0] if ips else "IP_NON_DETECTEE"
        text = (
            f"Main: {self.main_name_var.get().strip() or 'Main'}\n"
            f"IP: {ip_text}\n"
            f"Port TCP: {self.port_var.get().strip()}\n"
            f"Port découverte: {self.discovery_port_var.get().strip()}\n"
            f"Clé: {self.pairing_key_var.get().strip()}"
        )
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update_idletasks()
        self._log("Informations de connexion copiées dans le presse-papiers.", "success")

    def _process_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                try:
                    self._handle_event(event)
                except Exception as exc:
                    self._log(f"Événement interne ignoré: {exc}", "error")
        except queue.Empty:
            pass
        self.root.after(100, self._process_events)

    def _handle_event(self, event: dict[str, Any]) -> None:
        generation = event.get("_generation")
        if generation is not None and int(generation) != self.service_generation:
            return
        event_type = event.get("type")
        if event_type == "server_started":
            self.server_status_var.set(f"Serveur: TCP {event['port']} actif")
            self.server_status_label.configure(style="Good.Status.TLabel")
            self._log(
                f"Serveur TCP démarré sur le port {event['port']}. Découverte UDP: {event['discovery_port']}.",
                "success",
            )
        elif event_type == "server_stopped":
            self.server_status_var.set("Serveur: arrêté")
            self.server_status_label.configure(style="Warn.Status.TLabel")
        elif event_type == "discovery_error":
            self._log(f"Découverte automatique indisponible: {event.get('message', '')}", "warning")
        elif event_type == "hotkey_started":
            self._log(
                f"Écoute {action_label(event.get('action'))}: {event.get('key')} ({event.get('chord')}).",
                "success",
            )
        elif event_type == "hotkey_ignored":
            self._log(
                f"{action_label(event.get('action'))} ignorée ({event.get('key')}): fenêtre active "
                f"{event.get('process')}, attendu {event.get('expected')}.",
                "warning",
            )
        elif event_type == "hotkey_error":
            self._log(
                f"Erreur de détection {action_label(event.get('action'))}: {event.get('message')}",
                "error",
            )
        elif event_type == "peer_connected":
            keys = event.get("action_keys") if isinstance(event.get("action_keys"), dict) else {}
            self._log(
                f"Box connectée: {event.get('name')} ({event.get('ip')}), "
                f"Attaquer={keys.get(ACTION_ATTACK, '?')!r}, Suivre={keys.get(ACTION_FOLLOW, '?')!r}.",
                "success",
            )
        elif event_type == "peer_disconnected":
            self._log(f"Box déconnectée: {event.get('name')} ({event.get('ip')}).", "warning")
        elif event_type == "auth_failed":
            self._log(f"Connexion refusée depuis {event.get('ip')}: clé d'appairage incorrecte.", "error")
        elif event_type == "broadcast":
            sent = int(event.get("sent", 0))
            connected = int(event.get("connected", 0))
            label = action_label(event.get("action"))
            source = "test manuel" if event.get("source") == "button" else f"touche {event.get('trigger')}"
            level = "success" if sent else "warning"
            self._log(f"{label} envoyé par {source}: {sent}/{connected} Box.", level)
        elif event_type == "ack":
            level = "success" if event.get("ok") else "error"
            prefix = "OK" if event.get("ok") else "ÉCHEC"
            self._log(
                f"{prefix} {event.get('name')} [{action_label(event.get('action'))}]: {event.get('detail')}",
                level,
            )
        elif event_type == "peer_timeout":
            self._log(f"Timeout réseau pour {event.get('name')} ({event.get('ip')}).", "warning")
        elif event_type == "network_error":
            self._log(f"Erreur réseau avec {event.get('ip')}: {event.get('message')}", "error")
        elif event_type == "manual_broadcast_error":
            self._log(f"Test réseau impossible: {event.get('message')}", "error")

    def _refresh_runtime_status(self) -> None:
        try:
            info = get_foreground_info()
            target = self.target_process_var.get().strip()
            if info.matches(target):
                self.focus_status_var.set(f"Focus: {info.process_name} actif")
                self.focus_status_label.configure(style="Good.Status.TLabel")
            else:
                active = info.process_name or "aucune fenêtre"
                self.focus_status_var.set(f"Focus: {active}")
                self.focus_status_label.configure(style="Warn.Status.TLabel")
        finally:
            self.root.after(250, self._refresh_runtime_status)

    def _refresh_peer_tree(self) -> None:
        peers = self.server.peer_snapshot() if self.server else []
        self.box_count_var.set(f"Box connectées: {len(peers)}")
        self.box_count_label.configure(style="Good.Status.TLabel" if peers else "Status.TLabel")

        existing = set(self.peer_tree.get_children())
        current_ids: set[str] = set()
        for peer in peers:
            peer_id = str(peer["client_id"])
            current_ids.add(peer_id)
            keys = peer.get("action_keys") if isinstance(peer.get("action_keys"), dict) else {}
            values = (
                peer["name"],
                peer["ip"],
                keys.get(ACTION_ATTACK, "?"),
                keys.get(ACTION_FOLLOW, "?"),
                format_clock(float(peer["connected_at"])),
                peer["last_result"],
            )
            if peer_id in existing:
                self.peer_tree.item(peer_id, values=values)
            else:
                self.peer_tree.insert("", "end", iid=peer_id, values=values)
        for stale in existing - current_ids:
            self.peer_tree.delete(stale)

        self.root.after(600, self._refresh_peer_tree)

    def _log(self, message: str, level: str = "info") -> None:
        append_log(self.log_widget, f"[{format_clock()}] {message}", level)

    def _on_close(self) -> None:
        self._stop_services()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    MainAssistApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

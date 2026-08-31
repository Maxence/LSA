from __future__ import annotations

import hashlib
import queue
import socket
import threading
import time
import tkinter as tk
import uuid
from tkinter import messagebox, ttk
from typing import Any

from assist_common import (
    ACTION_ATTACK,
    ACTION_FOLLOW,
    APP_DIR,
    ConfigError,
    action_label,
    chord_signature,
    format_clock,
    get_foreground_info,
    is_running_as_admin,
    load_json_config,
    normalize_action,
    resolve_key_spec,
    save_json_config,
    validate_pairing_key,
    validate_port,
)
from assist_network import BoxAssistClient, discover_main
from logitech_input import DRIVER_CODES, DRIVER_DISPLAY_NAMES, LogitechInput
from ui_utils import add_labeled_entry, append_log, apply_dark_style, make_log_widget


APP_VERSION = "2.0"
CONFIG_PATH = APP_DIR / "box_settings.json"

BOX_DEFAULTS: dict[str, Any] = {
    "box_name": "Box 1",
    "main_host": "AUTO",
    "port": 45880,
    "discovery_port": 45881,
    "pairing_key": "",
    "attack_output_key": "&",
    "follow_output_key": "VK_2",
    "hold_ms": 45,
    "target_process": "L2.exe",
    "require_target_foreground": True,
    "driver": "Logitech",
    "dll_path": "IbInputSimulator.dll",
    "reconnect_delay_sec": 2.0,
}


def make_client_id(box_name: str) -> str:
    machine = f"{socket.gethostname()}|{uuid.getnode()}|{box_name.strip().lower()}"
    return "box-" + hashlib.sha256(machine.encode("utf-8", errors="ignore")).hexdigest()[:24]


class BoxAssistApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"L2 Box Assist v{APP_VERSION}")
        self.root.geometry("1040x860")
        self.root.minsize(900, 720)
        apply_dark_style(root)

        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.service_generation = 0
        self.client: BoxAssistClient | None = None
        self.driver: LogitechInput | None = None
        self.config = self._load_config()
        self.current_client_id = make_client_id(str(self.config["box_name"]))
        self.peers: list[dict[str, Any]] = []
        self._was_connected = False
        self._last_log_times: dict[str, float] = {}

        self._build_variables()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._start_services)
        self.root.after(100, self._process_events)
        self.root.after(250, self._refresh_runtime_status)

    def _load_config(self) -> dict[str, Any]:
        try:
            config = load_json_config(CONFIG_PATH, BOX_DEFAULTS)
        except ConfigError as exc:
            messagebox.showerror("Configuration", str(exc))
            config = dict(BOX_DEFAULTS)

        changed = False
        legacy_marker = object()
        legacy_output = config.pop("output_key", legacy_marker)
        if legacy_output is not legacy_marker:
            if str(legacy_output or "").strip():
                config["attack_output_key"] = str(legacy_output)
            changed = True
        if changed:
            try:
                save_json_config(CONFIG_PATH, config)
            except ConfigError:
                pass
        return config

    def _build_variables(self) -> None:
        self.box_name_var = tk.StringVar(value=str(self.config["box_name"]))
        self.main_host_var = tk.StringVar(value=str(self.config["main_host"]))
        self.port_var = tk.StringVar(value=str(self.config["port"]))
        self.discovery_port_var = tk.StringVar(value=str(self.config["discovery_port"]))
        self.pairing_key_var = tk.StringVar(value=str(self.config["pairing_key"]))
        self.attack_output_key_var = tk.StringVar(value=str(self.config["attack_output_key"]))
        self.follow_output_key_var = tk.StringVar(value=str(self.config["follow_output_key"]))
        self.hold_ms_var = tk.StringVar(value=str(self.config["hold_ms"]))
        self.target_process_var = tk.StringVar(value=str(self.config["target_process"]))
        self.require_focus_var = tk.BooleanVar(value=bool(self.config["require_target_foreground"]))
        self.driver_var = tk.StringVar(value=str(self.config["driver"]))
        self.dll_path_var = tk.StringVar(value=str(self.config["dll_path"]))

        self.connection_status_var = tk.StringVar(value="Connexion: démarrage…")
        self.driver_status_var = tk.StringVar(value="Driver: initialisation…")
        self.focus_status_var = tk.StringVar(value="Focus: détection…")
        self.admin_status_var = tk.StringVar(
            value="Privilèges: administrateur" if is_running_as_admin() else "Privilèges: standard"
        )
        self.main_detail_var = tk.StringVar(value="Main: non connecté")

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, style="Root.TFrame", padding=14)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text=f"L2 Box Assist v{APP_VERSION}", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="Reçoit Attaquer ou Suivre du Main, puis joue la touche correspondante via le driver Logitech.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 12))

        status = ttk.Frame(outer)
        status.pack(fill="x", pady=(0, 10))
        for column in range(4):
            status.columnconfigure(column, weight=1)
        self.connection_status_label = ttk.Label(
            status, textvariable=self.connection_status_var, style="Status.TLabel"
        )
        self.connection_status_label.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.driver_status_label = ttk.Label(status, textvariable=self.driver_status_var, style="Status.TLabel")
        self.driver_status_label.grid(row=0, column=1, sticky="ew", padx=5)
        self.focus_status_label = ttk.Label(status, textvariable=self.focus_status_var, style="Status.TLabel")
        self.focus_status_label.grid(row=0, column=2, sticky="ew", padx=5)
        admin_style = "Good.Status.TLabel" if is_running_as_admin() else "Warn.Status.TLabel"
        ttk.Label(status, textvariable=self.admin_status_var, style=admin_style).grid(
            row=0, column=3, sticky="ew", padx=(5, 0)
        )

        settings = ttk.LabelFrame(outer, text="Configuration de cette Box", padding=10)
        settings.pack(fill="x", pady=(0, 10))
        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(3, weight=1)

        add_labeled_entry(settings, 0, "Nom de la Box", self.box_name_var, column=0, width=24)
        add_labeled_entry(settings, 0, "Adresse du Main", self.main_host_var, column=2, width=18)
        add_labeled_entry(settings, 1, "Port TCP", self.port_var, column=0, width=24)
        add_labeled_entry(settings, 1, "Port découverte UDP", self.discovery_port_var, column=2, width=18)
        add_labeled_entry(settings, 2, "Clé d'appairage", self.pairing_key_var, column=0, width=24)

        ttk.Label(settings, text="Backend driver").grid(row=2, column=2, sticky="w", padx=(0, 8), pady=4)
        driver_combo = ttk.Combobox(
            settings,
            textvariable=self.driver_var,
            values=DRIVER_DISPLAY_NAMES,
            state="readonly",
            width=22,
        )
        driver_combo.grid(row=2, column=3, sticky="ew", pady=4)

        add_labeled_entry(settings, 3, "Touche Box - Attaquer", self.attack_output_key_var, column=0, width=24)
        add_labeled_entry(settings, 3, "Touche Box - Suivre", self.follow_output_key_var, column=2, width=18)
        add_labeled_entry(settings, 4, "Processus du jeu", self.target_process_var, column=0, width=24)
        add_labeled_entry(settings, 4, "Durée appui (ms)", self.hold_ms_var, column=2, width=18)
        add_labeled_entry(settings, 5, "Chemin de la DLL", self.dll_path_var, column=0, width=24)

        focus_check = ttk.Checkbutton(
            settings,
            text="Ne jamais injecter une touche si Lineage 2 n'est pas au premier plan sur cette Box",
            variable=self.require_focus_var,
        )
        focus_check.grid(row=6, column=0, columnspan=4, sticky="w", pady=(5, 2))

        ttk.Label(
            settings,
            text="Par défaut: Attaquer joue '&' (touche physique 1 en AZERTY), Suivre joue VK_2 (touche physique 2).",
            style="Muted.TLabel",
        ).grid(row=7, column=0, columnspan=4, sticky="w", pady=(2, 0))

        button_row = ttk.Frame(settings)
        button_row.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(9, 0))
        ttk.Button(
            button_row,
            text="Enregistrer et reconnecter",
            style="Accent.TButton",
            command=self._save_and_reconnect,
        ).pack(side="left")
        ttk.Button(button_row, text="Détecter le Main", command=self._manual_discover).pack(side="left", padx=7)
        ttk.Button(
            button_row,
            text="Tester Attaquer",
            command=lambda: self._test_local_action(ACTION_ATTACK),
        ).pack(side="left")
        ttk.Button(
            button_row,
            text="Tester Suivre",
            command=lambda: self._test_local_action(ACTION_FOLLOW),
        ).pack(side="left", padx=(7, 0))

        main_line = ttk.Frame(outer)
        main_line.pack(fill="x", pady=(0, 8))
        ttk.Label(main_line, textvariable=self.main_detail_var, style="Muted.TLabel").pack(side="left")
        ttk.Label(
            main_line,
            text="La Box se reconnecte automatiquement en cas de coupure.",
            style="Muted.TLabel",
        ).pack(side="right")

        peers_frame = ttk.LabelFrame(outer, text="Machines visibles via le Main", padding=8)
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
        self.peer_tree.column("name", width=155, anchor="w")
        self.peer_tree.column("ip", width=120, anchor="w")
        self.peer_tree.column("attack", width=80, anchor="center")
        self.peer_tree.column("follow", width=80, anchor="center")
        self.peer_tree.column("connected", width=110, anchor="center")
        self.peer_tree.column("result", width=360, anchor="w")
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
        box_name = self.box_name_var.get().strip() or "Box"
        main_host = self.main_host_var.get().strip() or "AUTO"
        pairing_key = validate_pairing_key(self.pairing_key_var.get())

        attack_key = self.attack_output_key_var.get().strip()
        follow_key = self.follow_output_key_var.get().strip()
        foreground = get_foreground_info()
        attack_chord = resolve_key_spec(attack_key, foreground.keyboard_layout)
        follow_chord = resolve_key_spec(follow_key, foreground.keyboard_layout)
        if chord_signature(attack_chord) == chord_signature(follow_chord):
            raise ConfigError("Les touches Box Attaquer et Suivre doivent être différentes.")

        target_process = self.target_process_var.get().strip()
        if not target_process:
            raise ConfigError("Le processus du jeu ne peut pas être vide.")

        driver = self.driver_var.get().strip()
        if driver.lower() not in DRIVER_CODES:
            raise ConfigError(f"Backend driver inconnu: {driver}")

        try:
            hold_ms = int(self.hold_ms_var.get())
        except ValueError as exc:
            raise ConfigError("La durée d'appui doit être un nombre entier.") from exc
        if not 10 <= hold_ms <= 2000:
            raise ConfigError("La durée d'appui doit être comprise entre 10 et 2000 ms.")

        return {
            "box_name": box_name,
            "main_host": main_host,
            "port": validate_port(self.port_var.get()),
            "discovery_port": validate_port(self.discovery_port_var.get()),
            "pairing_key": pairing_key,
            "attack_output_key": attack_key,
            "follow_output_key": follow_key,
            "hold_ms": hold_ms,
            "target_process": target_process,
            "require_target_foreground": bool(self.require_focus_var.get()),
            "driver": driver,
            "dll_path": self.dll_path_var.get().strip() or "IbInputSimulator.dll",
            "reconnect_delay_sec": 2.0,
        }

    def _start_services(self) -> None:
        self._stop_services()
        self.service_generation += 1
        generation = self.service_generation
        try:
            config = self._collect_config()
        except Exception as exc:
            self.connection_status_var.set("Connexion: configuration invalide")
            self.connection_status_label.configure(style="Bad.Status.TLabel")
            self._log(f"Démarrage impossible: {exc}", "error")
            return

        self.config = config
        self.current_client_id = make_client_id(str(config["box_name"]))
        self.connection_status_var.set("Connexion: démarrage…")
        self.connection_status_label.configure(style="Status.TLabel")
        self.driver_status_var.set("Driver: initialisation…")
        self.driver_status_label.configure(style="Status.TLabel")

        driver = LogitechInput(str(config["dll_path"]), str(config["driver"]))
        self.driver = driver
        if driver.initialize():
            self.driver_status_var.set(f"Driver: {config['driver']} prêt")
            self.driver_status_label.configure(style="Good.Status.TLabel")
            self._log(f"IbInputSimulator initialisé avec le backend {config['driver']}.", "success")
        else:
            self.driver_status_var.set("Driver: erreur")
            self.driver_status_label.configure(style="Bad.Status.TLabel")
            self._log(driver.last_error, "error")

        client = BoxAssistClient(
            client_id=self.current_client_id,
            box_name=str(config["box_name"]),
            action_keys={
                ACTION_ATTACK: str(config["attack_output_key"]),
                ACTION_FOLLOW: str(config["follow_output_key"]),
            },
            main_host=str(config["main_host"]),
            port=int(config["port"]),
            discovery_port=int(config["discovery_port"]),
            pairing_key=str(config["pairing_key"]),
            reconnect_delay=float(config["reconnect_delay_sec"]),
            action_handler=lambda message, cfg=dict(config), drv=driver: self._handle_remote_action(
                cfg, drv, message
            ),
            event_callback=lambda event, gen=generation: self._emit_generation_event(gen, event),
        )
        self.client = client
        client.start()

    def _stop_services(self) -> None:
        if self.client:
            self.client.stop()
            self.client = None
        if self.driver:
            self.driver.shutdown()
            self.driver = None
        self._was_connected = False
        if hasattr(self, "peer_tree"):
            self._update_peers([])

    def _handle_remote_action(
        self,
        config: dict[str, Any],
        driver: LogitechInput,
        message: dict[str, Any],
    ) -> tuple[bool, str]:
        action = normalize_action(message.get("action"))
        if action == ACTION_ATTACK:
            output_key = str(config["attack_output_key"])
        elif action == ACTION_FOLLOW:
            output_key = str(config["follow_output_key"])
        else:
            return False, "Action inconnue reçue du Main."

        foreground = get_foreground_info()
        if bool(config["require_target_foreground"]) and not foreground.matches(str(config["target_process"])):
            active = foreground.process_name or "aucune fenêtre"
            return False, f"Injection annulée: fenêtre active {active}, attendu {config['target_process']}."

        result = driver.tap(
            output_key,
            int(config["hold_ms"]),
            foreground.keyboard_layout,
        )
        return result.ok, result.message

    def _save_and_reconnect(self) -> None:
        try:
            config = self._collect_config()
            save_json_config(CONFIG_PATH, config)
        except Exception as exc:
            messagebox.showerror("Configuration invalide", str(exc))
            return
        self.config = config
        self._log("Configuration enregistrée. Reconnexion…", "info")
        self._start_services()

    def _manual_discover(self) -> None:
        try:
            discovery_port = validate_port(self.discovery_port_var.get())
        except ConfigError as exc:
            messagebox.showerror("Port invalide", str(exc))
            return
        self._log(f"Recherche d'un Main sur le port UDP {discovery_port}…", "info")
        generation = self.service_generation

        def worker() -> None:
            found = discover_main(discovery_port, timeout=2.0)
            self._emit_generation_event(
                generation,
                {"type": "manual_discovery_result", "time": time.time(), "found": found},
            )

        threading.Thread(target=worker, name="BoxAssist-ManualDiscovery", daemon=True).start()

    def _test_local_action(self, action: str) -> None:
        try:
            config = self._collect_config()
        except Exception as exc:
            messagebox.showerror("Configuration invalide", str(exc))
            return
        driver = self.driver
        if not driver:
            self._log("Le driver n'est pas initialisé.", "error")
            return
        self._log(f"Test local {action_label(action)} demandé…", "info")

        generation = self.service_generation

        def worker() -> None:
            ok, detail = self._handle_remote_action(config, driver, {"type": "test", "action": action})
            self._emit_generation_event(
                generation,
                {
                    "type": "local_test_result",
                    "time": time.time(),
                    "action": action,
                    "ok": ok,
                    "detail": detail,
                },
            )

        threading.Thread(target=worker, name=f"BoxAssist-Test-{action}", daemon=True).start()

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
        if event_type == "discovering":
            self.connection_status_var.set("Connexion: recherche du Main…")
            self.connection_status_label.configure(style="Warn.Status.TLabel")
        elif event_type == "discovery_failed":
            self._log_throttled(
                "discovery_failed",
                "Aucun Main détecté automatiquement. L'adresse IP peut être saisie manuellement.",
                "warning",
                10.0,
            )
        elif event_type == "discovered":
            self._log_throttled(
                "discovered",
                f"Main détecté: {event.get('main_name')} sur {event.get('host')}:{event.get('port')}.",
                "success",
                5.0,
            )
        elif event_type == "connecting":
            self.connection_status_var.set(f"Connexion: {event.get('host')}:{event.get('port')}…")
            self.connection_status_label.configure(style="Warn.Status.TLabel")
        elif event_type == "connected":
            self._was_connected = True
            main_name = event.get("main_name", "Main")
            self.connection_status_var.set("Connexion: active")
            self.connection_status_label.configure(style="Good.Status.TLabel")
            self.main_detail_var.set(f"Main: {main_name} @ {event.get('host')}:{event.get('port')}")
            self._log(f"Connectée à {main_name} ({event.get('host')}:{event.get('port')}).", "success")
            boxes = event.get("boxes")
            if isinstance(boxes, list):
                self._update_peers(boxes)
        elif event_type == "peers":
            boxes = event.get("boxes")
            if isinstance(boxes, list):
                self._update_peers(boxes)
        elif event_type == "action_received":
            self._log(
                f"Commande {action_label(event.get('action'))} reçue du Main: "
                f"déclencheur {event.get('trigger')}, séquence {event.get('sequence')}.",
                "info",
            )
        elif event_type == "action_result":
            ok = bool(event.get("ok"))
            self._log(
                f"{action_label(event.get('action'))}: {event.get('detail', 'Résultat inconnu')}",
                "success" if ok else "error",
            )
        elif event_type == "server_error":
            self._log_throttled("server_error", str(event.get("message")), "error", 5.0)
        elif event_type == "connection_error":
            self._log_throttled(
                "connection_error",
                f"Connexion impossible à {event.get('host')}:{event.get('port')}: {event.get('message')}",
                "warning",
                8.0,
            )
        elif event_type == "disconnected":
            self.connection_status_var.set("Connexion: hors ligne")
            self.connection_status_label.configure(style="Warn.Status.TLabel")
            self.main_detail_var.set("Main: non connecté, reconnexion automatique")
            self._update_peers([])
            if self._was_connected:
                self._log(f"Déconnectée du Main: {event.get('reason')}", "warning")
            self._was_connected = False
        elif event_type == "manual_discovery_result":
            found = event.get("found")
            if not found:
                self._log("Aucun Main trouvé sur le réseau local.", "warning")
            else:
                self.main_host_var.set(str(found["host"]))
                self.port_var.set(str(found["port"]))
                self._log(
                    f"Main détecté: {found.get('main_name')} sur {found['host']}:{found['port']}. Connexion…",
                    "success",
                )
                self._save_and_reconnect()
        elif event_type == "local_test_result":
            self._log(
                f"Test local {action_label(event.get('action'))}: {event.get('detail')}",
                "success" if event.get("ok") else "error",
            )

    def _update_peers(self, peers: list[dict[str, Any]]) -> None:
        self.peers = peers
        existing = set(self.peer_tree.get_children())
        current_ids: set[str] = set()
        for peer in peers:
            client_id = str(peer.get("client_id", ""))
            if not client_id:
                continue
            current_ids.add(client_id)
            name = str(peer.get("name", "Box"))
            if client_id == self.current_client_id:
                name += " (cette Box)"
            keys = peer.get("action_keys") if isinstance(peer.get("action_keys"), dict) else {}
            values = (
                name,
                peer.get("ip", ""),
                keys.get(ACTION_ATTACK, "?"),
                keys.get(ACTION_FOLLOW, "?"),
                format_clock(float(peer.get("connected_at", time.time()))),
                peer.get("last_result", "En attente"),
            )
            if client_id in existing:
                self.peer_tree.item(client_id, values=values)
            else:
                self.peer_tree.insert("", "end", iid=client_id, values=values)
        for stale in existing - current_ids:
            self.peer_tree.delete(stale)

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

    def _log_throttled(self, key: str, message: str, level: str, interval: float) -> None:
        now = time.monotonic()
        if now - self._last_log_times.get(key, 0.0) < interval:
            return
        self._last_log_times[key] = now
        self._log(message, level)

    def _log(self, message: str, level: str = "info") -> None:
        append_log(self.log_widget, f"[{format_clock()}] {message}", level)

    def _on_close(self) -> None:
        self._stop_services()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    BoxAssistApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Any

import box_assist as base
from assist_common import action_label, get_foreground_info, save_json_config
from logitech_input import LogitechInput
from window_targeting import (
    TargetWindow,
    activate_window,
    foreground_hwnd,
    is_window_valid,
    list_target_windows,
    restore_minimized_state,
)


APP_VERSION = "2.1"
MULTI_WINDOW_FOCUS_SETTLE_SEC = 0.060
MULTI_DEFAULTS: dict[str, Any] = {
    "multi_window_enabled": False,
}

# Keep the title/header produced by the existing UI in sync with this wrapper.
base.APP_VERSION = APP_VERSION


class BoxAssistApp(base.BoxAssistApp):
    """Box Assist with an optional fan-out to every local Lineage 2 window."""

    def __init__(self, root: tk.Tk) -> None:
        self._multi_action_lock = threading.RLock()
        super().__init__(root)

    def _load_config(self) -> dict[str, Any]:
        config = super()._load_config()
        changed = False
        for key, value in MULTI_DEFAULTS.items():
            if key not in config:
                config[key] = value
                changed = True
        if changed:
            try:
                save_json_config(base.CONFIG_PATH, config)
            except Exception:
                pass
        return config

    def _build_variables(self) -> None:
        super()._build_variables()
        self.multi_window_var = tk.BooleanVar(value=bool(self.config.get("multi_window_enabled", False)))

    def _build_ui(self) -> None:
        super()._build_ui()

        children = self.root.winfo_children()
        if not children:
            return
        outer = children[0]

        multi_frame = ttk.LabelFrame(outer, text="Multi-fenêtres L2 (optionnel)", padding=8)
        ttk.Checkbutton(
            multi_frame,
            text="Envoyer chaque commande à tous les L2.exe ouverts sur cette Box",
            variable=self.multi_window_var,
        ).pack(anchor="w")
        ttk.Label(
            multi_frame,
            text=(
                "Désactivé par défaut. Quand il est activé, la Box donne brièvement le focus à chaque client L2, "
                "envoie la touche Logitech, puis restaure la fenêtre qui était active."
            ),
            style="Muted.TLabel",
            wraplength=940,
            justify="left",
        ).pack(anchor="w", pady=(3, 0))
        ttk.Label(
            multi_frame,
            text=(
                "Si la sécurité de focus ci-dessus reste cochée, une fenêtre L2 doit déjà être active avant le fan-out."
            ),
            style="Muted.TLabel",
            wraplength=940,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

        peers_frame = None
        for child in outer.winfo_children():
            try:
                if isinstance(child, ttk.LabelFrame) and child.cget("text") == "Machines visibles via le Main":
                    peers_frame = child
                    break
            except Exception:
                continue

        if peers_frame is not None:
            multi_frame.pack(fill="x", pady=(0, 10), before=peers_frame)
        else:
            multi_frame.pack(fill="x", pady=(0, 10))

    def _collect_config(self) -> dict[str, Any]:
        config = super()._collect_config()
        config["multi_window_enabled"] = bool(self.multi_window_var.get())
        return config

    @staticmethod
    def _ordered_targets(targets: list[TargetWindow], original_hwnd: int) -> list[TargetWindow]:
        return sorted(targets, key=lambda item: (item.hwnd != original_hwnd, item.process_id, item.hwnd))

    def _handle_remote_action(
        self,
        config: dict[str, Any],
        driver: LogitechInput,
        message: dict[str, Any],
    ) -> tuple[bool, str]:
        if not bool(config.get("multi_window_enabled", False)):
            return super()._handle_remote_action(config, driver, message)

        action = base.normalize_action(message.get("action"))
        if action == base.ACTION_ATTACK:
            output_key = str(config["attack_output_key"])
        elif action == base.ACTION_FOLLOW:
            output_key = str(config["follow_output_key"])
        else:
            return False, "Action inconnue reçue du Main."

        with self._multi_action_lock:
            original = get_foreground_info()
            target_process = str(config["target_process"])
            if bool(config["require_target_foreground"]) and not original.matches(target_process):
                active = original.process_name or "aucune fenêtre"
                return False, f"Injection annulée: fenêtre active {active}, attendu {target_process}."

            targets = self._ordered_targets(list_target_windows(target_process), int(original.hwnd))
            if not targets:
                return False, f"Aucune fenêtre visible correspondant à {target_process} n'a été trouvée."

            success_count = 0
            failures: list[str] = []
            original_hwnd = int(original.hwnd)

            try:
                for index, target in enumerate(targets, start=1):
                    try:
                        if foreground_hwnd() != target.hwnd and not activate_window(target.hwnd):
                            failures.append(f"fenêtre {index}: focus refusé par Windows")
                            continue

                        time.sleep(MULTI_WINDOW_FOCUS_SETTLE_SEC)
                        if foreground_hwnd() != target.hwnd:
                            failures.append(f"fenêtre {index}: focus perdu avant l'envoi")
                            continue

                        result = driver.tap(output_key, int(config["hold_ms"]), target.keyboard_layout)
                        if result.ok:
                            success_count += 1
                        else:
                            failures.append(f"fenêtre {index}: {result.message}")
                    finally:
                        restore_minimized_state(target)
            finally:
                if original_hwnd and is_window_valid(original_hwnd) and foreground_hwnd() != original_hwnd:
                    activate_window(original_hwnd)

            total = len(targets)
            label = action_label(action)
            if success_count == total:
                return True, f"{label}: touche {output_key!r} envoyée à {success_count}/{total} fenêtre(s) L2."

            detail = "; ".join(failures[:3]) or "échec inconnu"
            return False, f"{label}: {success_count}/{total} fenêtre(s) L2 traitée(s). {detail}"


def main() -> None:
    root = tk.Tk()
    BoxAssistApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

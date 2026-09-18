"""Targeted Dance/Song dispatch. The caller serializes this with other actions."""
from __future__ import annotations

import time
from typing import Any

from assist_common import ConfigError, validate_character_name
from logitech_input import LogitechInput
from window_targeting import (
    activate_window,
    foreground_hwnd,
    is_window_valid,
    list_target_windows,
    matching_character_windows,
    restore_minimized_state,
    window_still_matches,
)

FOCUS_SETTLE_SEC = 0.060


def send_dance_song(
    config: dict[str, Any], driver: LogitechInput, message: dict[str, Any]
) -> tuple[bool, str]:
    """Send only to the unique local game window with the requested full title."""
    try:
        character = validate_character_name(message.get("target_character"))
    except ConfigError as exc:
        return False, f"Dance/Song annulé: {exc}"

    target_process = str(config["target_process"])
    targets = matching_character_windows(list_target_windows(target_process), character)
    if not targets:
        # Other PCs are expected to receive the command but have no such player.
        # This is an acknowledged no-op, never an injection or focus change.
        return True, f"Ignorée: {character!r} absent sur cette Box, aucune touche envoyée."
    if len(targets) != 1:
        return False, f"Dance/Song annulé: plusieurs fenêtres portent le pseudo {character!r}."

    output_key = str(config.get("dance_song_output_key", "")).strip()
    if not output_key:
        return False, "Dance/Song annulé: aucune touche Box configurée."

    target = targets[0]
    original_hwnd = foreground_hwnd()
    try:
        if foreground_hwnd() != target.hwnd and not activate_window(target.hwnd):
            return False, f"Dance/Song {character!r}: focus refusé par Windows, aucune touche envoyée."

        time.sleep(FOCUS_SETTLE_SEC)
        # The player can log out or a window can disappear while focus settles.
        refreshed = matching_character_windows(list_target_windows(target_process), character)
        if len(refreshed) != 1 or (refreshed[0].hwnd, refreshed[0].process_id) != (
            target.hwnd, target.process_id
        ):
            return False, f"Dance/Song {character!r}: cible disparue, modifiée ou ambiguë, envoi annulé."

        def ready() -> bool:
            # Check focus last, after process/title queries. LogitechInput also
            # calls this after lazy initialization, just before every key-down.
            return window_still_matches(target, target_process, character) and foreground_hwnd() == target.hwnd

        if not ready():
            return False, f"Dance/Song {character!r}: cible ou focus perdu avant l'envoi."
        result = driver.tap(
            output_key,
            int(config["hold_ms"]),
            refreshed[0].keyboard_layout,
            pre_send_check=ready,
        )
        if result.ok:
            return True, f"Dance/Song: touche {output_key!r} envoyée uniquement à {character!r}."
        return False, f"Dance/Song {character!r}: {result.message}"
    finally:
        restore_minimized_state(target)
        if original_hwnd and is_window_valid(original_hwnd) and foreground_hwnd() != original_hwnd:
            activate_window(original_hwnd)

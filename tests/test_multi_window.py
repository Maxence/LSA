from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assist_common import ACTION_ATTACK, ForegroundInfo
from box_assist_multi import BoxAssistApp
from window_targeting import TargetWindow


class MultiWindowBoxTests(unittest.TestCase):
    @staticmethod
    def _config(enabled: bool = True) -> dict:
        return {
            "attack_output_key": "&",
            "follow_output_key": "VK_2",
            "hold_ms": 45,
            "target_process": "L2.exe",
            "require_target_foreground": True,
            "multi_window_enabled": enabled,
        }

    def _app(self) -> BoxAssistApp:
        app = BoxAssistApp.__new__(BoxAssistApp)
        app._multi_action_lock = threading.RLock()
        return app

    def test_multi_mode_sends_to_each_l2_window_and_restores_original(self) -> None:
        app = self._app()
        windows = [
            TargetWindow(100, 1, 10, "L2.exe", keyboard_layout=111),
            TargetWindow(200, 2, 20, "L2.exe", keyboard_layout=222),
        ]
        taps: list[tuple[str, int, int]] = []
        activations: list[int] = []
        active = {"hwnd": 100}

        class FakeDriver:
            def tap(self, key: str, hold_ms: int, keyboard_layout: int):
                taps.append((key, hold_ms, keyboard_layout))
                return SimpleNamespace(ok=True, message="ok")

        def activate(hwnd: int) -> bool:
            activations.append(hwnd)
            active["hwnd"] = hwnd
            return True

        with patch("box_assist_multi.get_foreground_info", return_value=ForegroundInfo(
            hwnd=100, process_name="L2.exe", keyboard_layout=111
        )), patch("box_assist_multi.list_target_windows", return_value=windows), patch(
            "box_assist_multi.activate_window", side_effect=activate
        ), patch("box_assist_multi.foreground_hwnd", side_effect=lambda: active["hwnd"]), patch(
            "box_assist_multi.is_window_valid", return_value=True
        ), patch("box_assist_multi.restore_minimized_state"), patch("box_assist_multi.time.sleep", return_value=None):
            ok, detail = app._handle_remote_action(self._config(), FakeDriver(), {"action": ACTION_ATTACK})

        self.assertTrue(ok)
        self.assertEqual(taps, [("&", 45, 111), ("&", 45, 222)])
        self.assertEqual(activations, [200, 100])
        self.assertIn("2/2", detail)

    def test_multi_mode_keeps_focus_safety(self) -> None:
        app = self._app()

        class FailDriver:
            def tap(self, *_args, **_kwargs):
                self.fail("driver must not be called")

        with patch(
            "box_assist_multi.get_foreground_info",
            return_value=ForegroundInfo(hwnd=50, process_name="notepad.exe"),
        ), patch("box_assist_multi.list_target_windows") as list_windows:
            ok, detail = app._handle_remote_action(self._config(), FailDriver(), {"action": ACTION_ATTACK})

        self.assertFalse(ok)
        self.assertIn("notepad.exe", detail)
        list_windows.assert_not_called()

    def test_multi_mode_does_not_send_if_focus_activation_fails(self) -> None:
        app = self._app()
        windows = [TargetWindow(200, 2, 20, "L2.exe", keyboard_layout=222)]
        taps: list[str] = []

        class FakeDriver:
            def tap(self, key: str, *_args):
                taps.append(key)
                return SimpleNamespace(ok=True, message="ok")

        with patch("box_assist_multi.get_foreground_info", return_value=ForegroundInfo(
            hwnd=100, process_name="L2.exe", keyboard_layout=111
        )), patch("box_assist_multi.list_target_windows", return_value=windows), patch(
            "box_assist_multi.activate_window", return_value=False
        ), patch("box_assist_multi.foreground_hwnd", return_value=100), patch(
            "box_assist_multi.is_window_valid", return_value=True
        ), patch("box_assist_multi.time.sleep", return_value=None):
            ok, detail = app._handle_remote_action(self._config(), FakeDriver(), {"action": ACTION_ATTACK})

        self.assertFalse(ok)
        self.assertEqual(taps, [])
        self.assertIn("focus refusé", detail)

    def test_disabled_multi_mode_uses_existing_box_behavior(self) -> None:
        app = self._app()
        expected = (True, "legacy path")
        with patch("box_assist.BoxAssistApp._handle_remote_action", return_value=expected) as legacy:
            result = app._handle_remote_action(self._config(enabled=False), object(), {"action": ACTION_ATTACK})
        self.assertEqual(result, expected)
        legacy.assert_called_once()


if __name__ == "__main__":
    unittest.main()

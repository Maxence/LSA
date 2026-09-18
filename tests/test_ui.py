"""Real Tk widgets, without network services or keyboard injection."""
from __future__ import annotations

import json
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main_assist import MAIN_DEFAULTS, MainAssistApp
from box_assist_multi import BoxAssistApp


class DanceSongUiTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"A graphical desktop or Xvfb is required: {exc}")
        self.root.withdraw()
        self.enterContext(patch("tkinter.messagebox.showerror", side_effect=AssertionError("Unexpected configuration error")))
        self.addCleanup(self.root.destroy)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def main_app(self):
        path = Path(self.directory.name) / "main.json"
        path.write_text(json.dumps({**MAIN_DEFAULTS, "pairing_key": "TEST-PAIRING"}), encoding="utf-8")
        config_patch = patch("main_assist.CONFIG_PATH", path)
        service_patch = patch.object(MainAssistApp, "_start_services", return_value=None)
        config_patch.start()
        service_patch.start()
        self.addCleanup(config_patch.stop)
        self.addCleanup(service_patch.stop)
        return MainAssistApp(self.root), path

    def test_main_toggle_enables_only_targeted_controls(self):
        app, _ = self.main_app()
        for widget in (app.dance_song_trigger_entry, app.dance_song_character_entry, app.dance_song_test_button):
            self.assertTrue(widget.instate(["disabled"]))
        app.dance_song_enabled_var.set(True)
        app._update_dance_song_controls()
        for widget in (app.dance_song_trigger_entry, app.dance_song_character_entry, app.dance_song_test_button):
            self.assertFalse(widget.instate(["disabled"]))
        app.dance_song_enabled_var.set(False)
        app._update_dance_song_controls()
        self.assertTrue(app.dance_song_test_button.instate(["disabled"]))

    def test_main_saves_enabled_target_and_independent_keys(self):
        app, path = self.main_app()
        app.dance_song_enabled_var.set(True)
        app.dance_song_character_var.set("Muraki")
        app.dance_song_trigger_key_var.set("F10")
        app._save_and_restart()
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(saved["dance_song_enabled"])
        self.assertEqual(saved["dance_song_character"], "Muraki")
        self.assertEqual(saved["dance_song_trigger_key"], "F10")
        self.assertEqual((saved["attack_trigger_key"], saved["follow_trigger_key"]), ("F2", "F3"))

    def test_box_saves_third_output_key_without_disabling_multi_window(self):
        path = Path(self.directory.name) / "box.json"
        path.write_text(json.dumps({"attack_output_key": "F7", "follow_output_key": "F8"}), encoding="utf-8")
        with patch("box_assist.CONFIG_PATH", path), patch.object(BoxAssistApp, "_start_services", return_value=None):
            app = BoxAssistApp(self.root)
            app.pairing_key_var.set("TEST-PAIRING")
            app.multi_window_var.set(True)
            app.dance_song_output_key_var.set("F9")
            app._save_and_reconnect()
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(saved["multi_window_enabled"])
        self.assertEqual(saved["dance_song_output_key"], "F9")
        self.assertEqual((saved["attack_output_key"], saved["follow_output_key"]), ("F7", "F8"))


if __name__ == "__main__":
    unittest.main()

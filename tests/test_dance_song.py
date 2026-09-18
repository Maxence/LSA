from __future__ import annotations

import json
import queue
import sys
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assist_common import (
    ACTION_ATTACK, ACTION_DANCE_SONG, ACTION_FOLLOW,
    ConfigError, ForegroundInfo,
)
from assist_network import BoxAssistClient, MainAssistServer
from box_assist import BOX_DEFAULTS
from box_assist_multi import BoxAssistApp
from logitech_input import LogitechInput
from main_assist import MAIN_DEFAULTS, HotkeyWatcher, MainAssistApp
from targeted_action import send_dance_song
from window_targeting import TargetWindow, matching_character_windows, window_still_matches

MURAKI = TargetWindow(100, 1, 10, "L2.exe", keyboard_layout=111, title="Muraki")
OTHER = TargetWindow(200, 2, 20, "L2.exe", keyboard_layout=222, title="OtherPlayer")


def variable(value):
    return SimpleNamespace(get=lambda: value)


def box_app():
    app = BoxAssistApp.__new__(BoxAssistApp)
    app._action_lock = threading.RLock()
    return app


def config(**overrides):
    return {
        **BOX_DEFAULTS,
        "multi_window_enabled": True,
        "attack_output_key": "F7",
        "follow_output_key": "F8",
        "dance_song_output_key": "F9",
        **overrides,
    }


@contextmanager
def desktop(windows=None, original=200):
    """A mocked desktop, including a driver that honors the final focus guard."""
    state = SimpleNamespace(
        windows=list(windows if windows is not None else [MURAKI, OTHER]),
        hwnd=original, activations=[], taps=[], enumerations=0,
        activation_ok=True, identity_ok=True, result_ok=True,
    )

    def activate(hwnd):
        state.activations.append(hwnd)
        if state.activation_ok:
            state.hwnd = hwnd
        return state.activation_ok

    def tap(key, hold_ms, layout, *, pre_send_check):
        if not pre_send_check():
            return SimpleNamespace(ok=False, message="focus perdu")
        state.taps.append((key, state.hwnd, hold_ms, layout))
        return SimpleNamespace(ok=state.result_ok, message="driver result")

    def enumerate_windows(_process):
        state.enumerations += 1
        return list(state.windows)

    state.driver = SimpleNamespace(tap=tap)
    with ExitStack() as stack:
        for name, replacement in (
            ("list_target_windows", enumerate_windows),
            ("foreground_hwnd", lambda: state.hwnd),
            ("activate_window", activate),
            ("is_window_valid", lambda _hwnd: True),
            ("window_still_matches", lambda *_args: state.identity_ok),
        ):
            stack.enter_context(patch("targeted_action." + name, side_effect=replacement))
        state.restore = stack.enter_context(patch("targeted_action.restore_minimized_state"))
        state.sleep = stack.enter_context(patch("targeted_action.time.sleep"))
        yield state


class TargetedDispatchTests(unittest.TestCase):
    def test_targets_one_character_even_with_multi_window_enabled(self):
        with desktop() as pc:
            ok, detail = box_app()._handle_remote_action(
                config(), pc.driver, {"action": ACTION_DANCE_SONG, "target_character": "Muraki"}
            )
            self.assertTrue(ok, detail)
            self.assertEqual(pc.taps, [("F9", 100, 45, 111)])
            self.assertEqual(pc.activations, [100, 200])
            self.assertEqual(pc.hwnd, 200)

    def test_targets_character_with_multi_mode_disabled_and_non_l2_foreground(self):
        with desktop(original=999) as pc:
            ok, detail = box_app()._handle_remote_action(
                config(multi_window_enabled=False), pc.driver,
                {"action": ACTION_DANCE_SONG, "target_character": "Muraki"},
            )
            self.assertTrue(ok, detail)
            self.assertEqual(pc.taps[0][1], 100)
            self.assertEqual(pc.activations, [100, 999])

    def test_non_owner_pc_is_noop_without_focus_change(self):
        with desktop([OTHER, replace(MURAKI, title="MurakiAlt")]) as pc:
            ok, detail = send_dance_song(config(), pc.driver, {"target_character": "Muraki"})
            self.assertTrue(ok)
            self.assertIn("Ignorée", detail)
            self.assertEqual(pc.activations, [])
            self.assertEqual(pc.taps, [])
            pc.restore.assert_not_called()

    def test_full_title_match_is_case_insensitive(self):
        self.assertEqual(
            matching_character_windows([MURAKI, replace(OTHER, title="MurakiAlt")], " muraki "),
            [MURAKI],
        )
        self.assertEqual(matching_character_windows([MURAKI], "Mura"), [])
        self.assertEqual(matching_character_windows([MURAKI], ""), [])

    def test_ambiguous_character_does_not_choose_first_window(self):
        with desktop([MURAKI, replace(OTHER, title="muraki")]) as pc:
            ok, detail = send_dance_song(config(), pc.driver, {"target_character": "Muraki"})
            self.assertFalse(ok)
            self.assertIn("plusieurs", detail)
            self.assertEqual(pc.activations, [])
            self.assertEqual(pc.taps, [])

    def test_missing_or_invalid_target_never_falls_back(self):
        for target in (None, "", "  ", 123, [], "Mura\nki", "Muraki\n", "x" * 65):
            with self.subTest(target=target), desktop() as pc:
                ok, _ = send_dance_song(config(), pc.driver, {"target_character": target})
                self.assertFalse(ok)
                self.assertEqual(pc.enumerations, 0)
                self.assertEqual(pc.taps, [])

    def test_missing_output_mapping_never_uses_attack_or_follow(self):
        with desktop() as pc:
            ok, _ = send_dance_song(config(dance_song_output_key=""), pc.driver, {"target_character": "Muraki"})
            self.assertFalse(ok)
            self.assertEqual(pc.taps, [])
            self.assertEqual(pc.activations, [])

    def test_windows_refusing_focus_blocks_key(self):
        with desktop() as pc:
            pc.activation_ok = False
            ok, detail = send_dance_song(config(), pc.driver, {"target_character": "Muraki"})
            self.assertFalse(ok)
            self.assertIn("focus refusé", detail)
            self.assertEqual(pc.taps, [])

    def test_focus_loss_after_settle_blocks_key(self):
        with desktop() as pc:
            pc.sleep.side_effect = lambda _delay: setattr(pc, "hwnd", 200)
            ok, detail = send_dance_song(config(), pc.driver, {"target_character": "Muraki"})
            self.assertFalse(ok)
            self.assertIn("focus perdu", detail)
            self.assertEqual(pc.taps, [])

    def test_target_disappearing_changing_or_becoming_ambiguous_blocks_key(self):
        for replacement in ([], [replace(MURAKI, process_id=99)], [MURAKI, replace(OTHER, title="Muraki")]):
            with self.subTest(windows=replacement), desktop() as pc:
                pc.sleep.side_effect = lambda _delay: setattr(pc, "windows", replacement)
                ok, _ = send_dance_song(config(), pc.driver, {"target_character": "Muraki"})
                self.assertFalse(ok)
                self.assertEqual(pc.taps, [])
                self.assertEqual(pc.hwnd, 200)

    def test_live_title_or_process_change_blocks_key(self):
        with desktop() as pc:
            pc.identity_ok = False
            ok, _ = send_dance_song(config(), pc.driver, {"target_character": "Muraki"})
            self.assertFalse(ok)
            self.assertEqual(pc.taps, [])

    def test_last_moment_focus_loss_in_driver_blocks_key(self):
        with desktop() as pc:
            real_tap = pc.driver.tap

            def delayed_tap(*args, **kwargs):
                pc.hwnd = 200
                return real_tap(*args, **kwargs)

            pc.driver.tap = delayed_tap
            ok, _ = send_dance_song(config(), pc.driver, {"target_character": "Muraki"})
            self.assertFalse(ok)
            self.assertEqual(pc.taps, [])

    def test_minimized_state_is_restored(self):
        minimized = replace(MURAKI, was_minimized=True)
        with desktop([minimized, OTHER]) as pc:
            ok, _ = send_dance_song(config(), pc.driver, {"target_character": "Muraki"})
            self.assertTrue(ok)
            pc.restore.assert_called_once_with(minimized)
            self.assertEqual(pc.hwnd, 200)

    def test_driver_failure_restores_previous_focus(self):
        with desktop() as pc:
            pc.result_ok = False
            ok, detail = send_dance_song(config(), pc.driver, {"target_character": "Muraki"})
            self.assertFalse(ok)
            self.assertIn("driver result", detail)
            self.assertEqual(pc.hwnd, 200)

    def test_driver_exception_still_restores_previous_focus(self):
        with desktop() as pc:
            pc.driver.tap = Mock(side_effect=RuntimeError("test failure"))
            with self.assertRaises(RuntimeError):
                send_dance_song(config(), pc.driver, {"target_character": "Muraki"})
            self.assertEqual(pc.hwnd, 200)
            pc.restore.assert_called_once()

    def test_shared_lock_serializes_targeted_and_single_window_actions(self):
        app = box_app()
        entered, release, second_started = threading.Event(), threading.Event(), threading.Event()
        call_order = []

        def targeted(*_args):
            call_order.append("dance-start")
            entered.set()
            if not release.wait(2):
                raise AssertionError("Test timed out")
            call_order.append("dance-end")
            return True, "ok"

        driver = SimpleNamespace(tap=lambda *_args: (call_order.append("follow") or SimpleNamespace(ok=True, message="ok")))
        cfg = config(multi_window_enabled=False)

        def follow():
            second_started.set()
            app._handle_remote_action(cfg, driver, {"action": ACTION_FOLLOW})

        with patch("box_assist.send_dance_song", side_effect=targeted), patch(
            "box_assist.get_foreground_info", return_value=ForegroundInfo(process_name="L2.exe")
        ):
            one = threading.Thread(target=app._handle_remote_action, args=(cfg, driver, {"action": ACTION_DANCE_SONG}))
            two = threading.Thread(target=follow)
            try:
                one.start()
                self.assertTrue(entered.wait(2))
                two.start()
                self.assertTrue(second_started.wait(2))
                self.assertEqual(call_order, ["dance-start"])
            finally:
                release.set()
                one.join(2)
                if two.ident is not None:
                    two.join(2)
            self.assertFalse(one.is_alive())
            self.assertFalse(two.is_alive())
        self.assertEqual(call_order, ["dance-start", "dance-end", "follow"])


class IdentityTests(unittest.TestCase):
    def test_live_identity_checks_hwnd_pid_thread_process_and_whole_title(self):
        state = SimpleNamespace(pid=10, tid=1)

        def get_pid(_hwnd, pointer):
            pointer._obj.value = state.pid
            return state.tid

        with patch("window_targeting.is_window_valid", return_value=True), patch(
            "window_targeting._user32", SimpleNamespace(GetWindowThreadProcessId=get_pid)
        ), patch("window_targeting._process_name", return_value="L2.exe") as process, patch(
            "window_targeting._window_title", return_value="Muraki"
        ) as title:
            self.assertTrue(window_still_matches(MURAKI, "L2.exe", "muraki"))
            title.return_value = "MurakiAlt"
            self.assertFalse(window_still_matches(MURAKI, "L2.exe", "Muraki"))
            title.return_value = "Muraki"
            process.return_value = "notepad.exe"
            self.assertFalse(window_still_matches(MURAKI, "L2.exe", "Muraki"))
            process.return_value = "L2.exe"
            state.pid = 999
            self.assertFalse(window_still_matches(MURAKI, "L2.exe", "Muraki"))
            state.pid = 10
            state.tid = 999
            self.assertFalse(window_still_matches(MURAKI, "L2.exe", "Muraki"))

    def test_closed_hwnd_fails_without_querying_process(self):
        with patch("window_targeting.is_window_valid", return_value=False), patch(
            "window_targeting._process_name"
        ) as process:
            self.assertFalse(window_still_matches(MURAKI, "L2.exe", "Muraki"))
            process.assert_not_called()


class GuardedDriverTests(unittest.TestCase):
    def test_guard_runs_after_lazy_initialization_and_blocks_key_down(self):
        driver = LogitechInput("unused.dll")
        events = []
        with patch.object(driver, "initialize", side_effect=lambda: events.append("init") or True), patch.object(
            driver, "_send_event", side_effect=lambda *_args, **_kwargs: events.append("key")
        ):
            result = driver.tap("F10", pre_send_check=lambda: events.append("guard") or False)
        self.assertFalse(result.ok)
        self.assertEqual(events, ["init", "guard"])

    def test_guard_failure_after_modifier_releases_modifier_without_base_key(self):
        driver = LogitechInput("unused.dll")
        keys = []
        with patch.object(driver, "initialize", return_value=True), patch.object(
            driver, "_send_event", side_effect=lambda key, *, key_up: keys.append((key, key_up))
        ), patch("logitech_input.time.sleep"):
            result = driver.tap("CTRL+F10", pre_send_check=Mock(side_effect=[True, False]))
        self.assertFalse(result.ok)
        self.assertEqual(keys, [(0x11, False), (0x11, True)])

    def test_successful_guard_preserves_balanced_key_events(self):
        driver = LogitechInput("unused.dll")
        keys = []
        with patch.object(driver, "initialize", return_value=True), patch.object(
            driver, "_send_event", side_effect=lambda key, *, key_up: keys.append((key, key_up))
        ), patch("logitech_input.time.sleep"):
            result = driver.tap("F10", pre_send_check=lambda: True)
        self.assertTrue(result.ok)
        self.assertEqual(keys, [(0x79, False), (0x79, True)])


class ConfigurationTests(unittest.TestCase):
    @staticmethod
    def main_app(**overrides):
        cfg = {**MAIN_DEFAULTS, "pairing_key": "TEST-PAIRING", **overrides}
        app = MainAssistApp.__new__(MainAssistApp)
        app.config = cfg
        for name in ("main_name", "attack_trigger_key", "follow_trigger_key", "dance_song_enabled",
                     "dance_song_trigger_key", "dance_song_character", "target_process", "port",
                     "discovery_port", "pairing_key"):
            setattr(app, name + "_var", variable(cfg[name]))
        app.require_focus_var = variable(cfg["require_target_foreground"])
        return app

    def test_disabled_option_does_not_require_target_or_third_key(self):
        app = self.main_app(dance_song_trigger_key="", dance_song_character="")
        cfg = app._collect_config()
        self.assertFalse(cfg["dance_song_enabled"])
        self.assertEqual(cfg["attack_trigger_key"], "F2")
        self.assertEqual(cfg["follow_trigger_key"], "F3")

    def test_enabled_option_requires_nonempty_target(self):
        with self.assertRaises(ConfigError):
            self.main_app(dance_song_enabled=True)._collect_config()

    def test_enabled_option_validates_third_physical_key(self):
        for trigger in ("F2", "CTRL+F2", "F3", "SHIFT+F3"):
            with self.subTest(trigger=trigger), self.assertRaises(ConfigError):
                self.main_app(dance_song_enabled=True, dance_song_character="Muraki",
                              dance_song_trigger_key=trigger)._collect_config()

    def test_enabled_config_roundtrips_without_changing_existing_keys(self):
        cfg = self.main_app(dance_song_enabled=True, dance_song_character=" Muraki ",
                            attack_trigger_key="F6", follow_trigger_key="F7")._collect_config()
        self.assertEqual(cfg["dance_song_character"], "Muraki")
        self.assertEqual(cfg["dance_song_trigger_key"], "F10")
        self.assertEqual((cfg["attack_trigger_key"], cfg["follow_trigger_key"]), ("F6", "F7"))

    def test_existing_settings_default_to_disabled_and_preserve_custom_values(self):
        app = self.main_app()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "main_settings.json"
            path.write_text(json.dumps({"attack_trigger_key": "F6", "follow_trigger_key": "F7",
                                        "pairing_key": "TEST-PAIRING"}), encoding="utf-8")
            with patch("main_assist.CONFIG_PATH", path):
                cfg = app._load_config()
            self.assertFalse(cfg["dance_song_enabled"])
            self.assertEqual(cfg["dance_song_trigger_key"], "F10")
            self.assertEqual((cfg["attack_trigger_key"], cfg["follow_trigger_key"]), ("F6", "F7"))

    def test_main_transports_target_only_for_dance_song(self):
        app = self.main_app(dance_song_enabled=True, dance_song_character="Muraki")
        app.server = SimpleNamespace(running=True, broadcast_action=Mock())
        for action in (ACTION_ATTACK, ACTION_FOLLOW, ACTION_DANCE_SONG):
            app._on_hotkey_trigger(action, ForegroundInfo(process_name="L2.exe"))
        calls = app.server.broadcast_action.call_args_list
        self.assertEqual([call.kwargs["target_character"] for call in calls], ["", "", "Muraki"])
        self.assertEqual(calls[-1].kwargs["trigger"], "F10")

    def test_disabled_main_never_broadcasts_dance_song(self):
        app = self.main_app()
        app.server = SimpleNamespace(running=True, broadcast_action=Mock())
        with self.assertRaises(ConfigError):
            app._on_hotkey_trigger(ACTION_DANCE_SONG, ForegroundInfo())
        app.server.broadcast_action.assert_not_called()

    def test_hotkey_watcher_only_binds_opted_in_actions(self):
        watcher = HotkeyWatcher(action_triggers={ACTION_ATTACK: "F2", ACTION_FOLLOW: "F3"},
                                target_process="L2.exe", require_target_foreground=True,
                                poll_interval_ms=10, on_trigger=lambda *_args: None)
        self.assertEqual([binding.action for binding in watcher.bindings], [ACTION_ATTACK, ACTION_FOLLOW])

    def test_third_hotkey_held_does_not_repeat(self):
        # Three keys are polled at startup and on each tick. Holding F10 must not repeat it.
        states = [False, True, True, False, True]
        calls = 0
        actions = []
        watcher = HotkeyWatcher(
            action_triggers={ACTION_ATTACK: "F2", ACTION_FOLLOW: "F3", ACTION_DANCE_SONG: "F10"},
            target_process="L2.exe", require_target_foreground=True, poll_interval_ms=5,
            on_trigger=lambda *_args: None,
        )

        def is_down(chord, **_kwargs):
            nonlocal calls
            tick = min(calls // 3, len(states) - 1)
            calls += 1
            return chord.vk_code == 0x79 and states[tick]

        def trigger(action, _foreground):
            actions.append(action)
            if len(actions) == 2:
                watcher._stop_event.set()

        watcher.on_trigger = trigger
        with patch("main_assist.is_chord_down", side_effect=is_down), patch(
            "main_assist.get_foreground_info", return_value=ForegroundInfo(process_name="L2.exe")
        ):
            watcher._run()
        self.assertEqual(actions, [ACTION_DANCE_SONG, ACTION_DANCE_SONG])
        self.assertEqual(calls, 15)


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.events = queue.Queue()
        self.server = MainAssistServer(host="127.0.0.1", port=0, discovery_port=0,
                                       pairing_key="TEST-PAIRING", main_name="Main", event_callback=self.events.put)
        self.server.start()
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.stop()
        self.server.stop()

    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                event = self.events.get(timeout=0.1)
            except queue.Empty:
                continue
            if predicate(event):
                return event
        self.fail("Timed out waiting for network event")

    def connect(self, name, handler, *, supports_target=True):
        keys = {ACTION_ATTACK: "F7", ACTION_FOLLOW: "F8"}
        if supports_target:
            keys[ACTION_DANCE_SONG] = "F9"
        client = BoxAssistClient(client_id="box-test-" + name, box_name=name, action_keys=keys,
                                 main_host="127.0.0.1", port=self.server.port, discovery_port=0,
                                 pairing_key="TEST-PAIRING", reconnect_delay=0.5, action_handler=handler)
        self.clients.append(client)
        client.start()
        self.wait_for(lambda event: event.get("type") == "peer_connected" and event.get("name") == name)
        return client

    def test_three_pc_scenario_with_four_game_windows(self):
        received, outputs = [], {}
        simulation_lock = threading.Lock()

        def handler(name, windows):
            app = box_app()

            def run(message):
                # Desktop APIs are process-global mocks; serialize the simulated PCs.
                with simulation_lock, desktop(windows, original=200) as pc:
                    received.append(dict(message))
                    result = app._handle_remote_action(config(), pc.driver, message)
                    outputs[name] = (list(pc.taps), list(pc.activations), result)
                    return result
            return run

        self.connect("owner", handler("owner", [MURAKI, OTHER]))
        self.connect("other", handler("other", [replace(MURAKI, title="MurakiAlt"), OTHER]))
        event_id, sent = self.server.broadcast_action(action=ACTION_DANCE_SONG, trigger="F10", target_character="Muraki")
        self.assertEqual(sent, 2)
        for _ in range(2):
            self.wait_for(lambda event: event.get("type") == "ack" and event.get("event_id") == event_id)
        self.assertEqual(len(received), 2)
        self.assertTrue(all(message["target_character"] == "Muraki" for message in received))
        self.assertEqual(outputs["owner"][0], [("F9", 100, 45, 111)])
        self.assertEqual(outputs["owner"][1], [100, 200])
        self.assertEqual(outputs["other"][0], [])
        self.assertEqual(outputs["other"][1], [])
        self.assertIn("Ignorée", outputs["other"][2][1])

    def test_legacy_box_is_skipped_for_dance_but_still_receives_follow(self):
        old_received, new_received = queue.Queue(), queue.Queue()
        self.connect("old", lambda message: (old_received.put(message) or (True, "old")), supports_target=False)
        self.connect("new", lambda message: (new_received.put(message) or (True, "new")))
        _, sent = self.server.broadcast_action(action=ACTION_DANCE_SONG, trigger="F10", target_character="Muraki")
        self.assertEqual(sent, 1)
        self.assertEqual(new_received.get(timeout=3)["target_character"], "Muraki")
        event = self.wait_for(lambda event: event.get("type") == "broadcast")
        self.assertEqual(event["unsupported"], 1)
        self.assertTrue(old_received.empty())
        _, sent = self.server.broadcast_action(action=ACTION_FOLLOW, trigger="F3")
        self.assertEqual(sent, 2)
        for received in (old_received, new_received):
            message = received.get(timeout=3)
            self.assertEqual(message["action"], ACTION_FOLLOW)
            self.assertNotIn("target_character", message)

    def test_dance_without_valid_target_is_rejected_before_network_send(self):
        for target in ("", " \t ", "x" * 65, None):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.server.broadcast_action(action=ACTION_DANCE_SONG, trigger="F10", target_character=target)
        self.assertEqual(self.server._sequence, 0)

    def test_target_on_attack_or_follow_is_rejected_instead_of_broadcasting(self):
        for action in (ACTION_ATTACK, ACTION_FOLLOW):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.server.broadcast_action(action=action, trigger="F2", target_character="Muraki")
        self.assertEqual(self.server._sequence, 0)


if __name__ == "__main__":
    unittest.main()

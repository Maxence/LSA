from __future__ import annotations

import queue
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assist_common import (
    ACTION_ATTACK,
    ACTION_FOLLOW,
    ForegroundInfo,
    KeyChord,
    action_label,
    chord_signature,
    extract_json_lines,
    is_chord_down,
    normalize_action,
    parse_target_processes,
    resolve_key_spec,
)
from assist_network import BoxAssistClient, MainAssistServer
from box_assist import BOX_DEFAULTS, BoxAssistApp
from logitech_input import LogitechInput
from main_assist import MAIN_DEFAULTS, HotkeyWatcher, MainAssistApp


class CommonTests(unittest.TestCase):
    def test_extract_json_lines(self) -> None:
        messages, remainder = extract_json_lines(b'{"type":"ping"}\n{"type":"pong"}\npartial')
        self.assertEqual([message["type"] for message in messages], ["ping", "pong"])
        self.assertEqual(remainder, b"partial")

    def test_target_process_list(self) -> None:
        self.assertEqual(parse_target_processes("L2.exe; L2.bin, client.exe"), {"l2.exe", "l2.bin", "client.exe"})

    def test_named_keys_and_distinct_signatures(self) -> None:
        attack = resolve_key_spec("F2")
        follow = resolve_key_spec("F3")
        self.assertEqual(attack.vk_code, 0x71)
        self.assertEqual(follow.vk_code, 0x72)
        self.assertNotEqual(chord_signature(attack), chord_signature(follow))

    def test_explicit_chord(self) -> None:
        chord = resolve_key_spec("CTRL+F2")
        self.assertEqual(chord.vk_code, 0x71)
        self.assertIn(0x11, chord.modifiers)

    def test_exact_modifier_detection(self) -> None:
        chord = KeyChord(vk_code=0x71, modifiers=(), source="F2")

        def pressed_with_ctrl(vk_code: int) -> bool:
            return vk_code in {0x71, 0x11}

        with patch("assist_common.is_virtual_key_down", side_effect=pressed_with_ctrl):
            self.assertTrue(is_chord_down(chord))
            self.assertFalse(is_chord_down(chord, exact_modifiers=True))

    def test_action_helpers(self) -> None:
        self.assertEqual(normalize_action(" ATTACK "), ACTION_ATTACK)
        self.assertEqual(normalize_action("follow"), ACTION_FOLLOW)
        self.assertEqual(normalize_action("invalid"), "")
        self.assertEqual(action_label(ACTION_ATTACK), "Attaquer")
        self.assertEqual(action_label(ACTION_FOLLOW), "Suivre")


class ActionRoutingTests(unittest.TestCase):
    def test_hotkey_watcher_routes_each_edge_once(self) -> None:
        # Each row represents one polling pass: F2 state, then F3 state.
        states = [
            {0x71: False, 0x72: False},
            {0x71: True, 0x72: False},
            {0x71: True, 0x72: False},
            {0x71: False, 0x72: False},
            {0x71: False, 0x72: True},
        ]
        calls = 0
        actions: list[str] = []

        def fake_is_chord_down(chord: KeyChord, *, exact_modifiers: bool = False) -> bool:
            nonlocal calls
            self.assertTrue(exact_modifiers)
            poll_index = min(calls // 2, len(states) - 1)
            calls += 1
            return states[poll_index][chord.vk_code]

        watcher = HotkeyWatcher(
            action_triggers={ACTION_ATTACK: "F2", ACTION_FOLLOW: "F3"},
            target_process="L2.exe",
            require_target_foreground=True,
            poll_interval_ms=5,
            on_trigger=lambda action, _foreground: actions.append(action),
        )

        def route_and_stop(action: str, _foreground: ForegroundInfo) -> None:
            actions.append(action)
            if len(actions) == 2:
                watcher._stop_event.set()

        watcher.on_trigger = route_and_stop
        foreground = ForegroundInfo(process_name="L2.exe")
        with patch("main_assist.is_chord_down", side_effect=fake_is_chord_down), patch(
            "main_assist.get_foreground_info", return_value=foreground
        ):
            watcher._run()

        self.assertEqual(actions, [ACTION_ATTACK, ACTION_FOLLOW])

    def test_hotkey_already_held_at_start_requires_a_fresh_press(self) -> None:
        states = [
            {0x71: True, 0x72: False},   # state captured at watcher startup
            {0x71: True, 0x72: False},   # still held: must not trigger
            {0x71: False, 0x72: False},  # released
            {0x71: True, 0x72: False},   # fresh press: trigger once
        ]
        calls = 0
        trigger_call_counts: list[int] = []

        def fake_is_chord_down(chord: KeyChord, *, exact_modifiers: bool = False) -> bool:
            nonlocal calls
            self.assertTrue(exact_modifiers)
            poll_index = min(calls // 2, len(states) - 1)
            calls += 1
            return states[poll_index][chord.vk_code]

        watcher = HotkeyWatcher(
            action_triggers={ACTION_ATTACK: "F2", ACTION_FOLLOW: "F3"},
            target_process="L2.exe",
            require_target_foreground=True,
            poll_interval_ms=5,
            on_trigger=lambda _action, _foreground: None,
        )

        def route_and_stop(action: str, _foreground: ForegroundInfo) -> None:
            self.assertEqual(action, ACTION_ATTACK)
            trigger_call_counts.append(calls)
            watcher._stop_event.set()

        watcher.on_trigger = route_and_stop
        foreground = ForegroundInfo(process_name="L2.exe")
        with patch("main_assist.is_chord_down", side_effect=fake_is_chord_down), patch(
            "main_assist.get_foreground_info", return_value=foreground
        ):
            watcher._run()

        self.assertEqual(len(trigger_call_counts), 1)
        self.assertGreaterEqual(trigger_call_counts[0], 8)

    def test_box_maps_actions_to_their_own_keys(self) -> None:
        app = BoxAssistApp.__new__(BoxAssistApp)
        sent: list[tuple[str, int, int]] = []

        class FakeDriver:
            def tap(self, key: str, hold_ms: int, keyboard_layout: int):
                sent.append((key, hold_ms, keyboard_layout))
                return SimpleNamespace(ok=True, message=f"sent {key}")

        config = {
            "attack_output_key": "&",
            "follow_output_key": "VK_2",
            "hold_ms": 45,
            "target_process": "L2.exe",
            "require_target_foreground": True,
        }
        foreground = ForegroundInfo(process_name="L2.exe", keyboard_layout=1234)
        with patch("box_assist.get_foreground_info", return_value=foreground):
            attack = app._handle_remote_action(config, FakeDriver(), {"action": ACTION_ATTACK})
            follow = app._handle_remote_action(config, FakeDriver(), {"action": ACTION_FOLLOW})

        self.assertTrue(attack[0])
        self.assertTrue(follow[0])
        self.assertEqual(sent, [("&", 45, 1234), ("VK_2", 45, 1234)])

    def test_box_cancels_injection_outside_lineage(self) -> None:
        app = BoxAssistApp.__new__(BoxAssistApp)

        class FailIfCalledDriver:
            def tap(self, *_args, **_kwargs):
                self.fail("Driver should not be called outside Lineage 2")

        config = {
            "attack_output_key": "&",
            "follow_output_key": "VK_2",
            "hold_ms": 45,
            "target_process": "L2.exe",
            "require_target_foreground": True,
        }
        with patch("box_assist.get_foreground_info", return_value=ForegroundInfo(process_name="notepad.exe")):
            ok, detail = app._handle_remote_action(config, FailIfCalledDriver(), {"action": ACTION_ATTACK})

        self.assertFalse(ok)
        self.assertIn("notepad.exe", detail)


class ConfigurationMigrationTests(unittest.TestCase):
    def test_main_v1_trigger_is_migrated_to_attack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "main_settings.json"
            path.write_text(
                json.dumps(
                    {
                        "main_name": "Old Main",
                        "port": 45880,
                        "discovery_port": 45881,
                        "pairing_key": "OLD-PAIRING",
                        "trigger_key": "F8",
                        "target_process": "L2.exe",
                    }
                ),
                encoding="utf-8",
            )
            app = MainAssistApp.__new__(MainAssistApp)
            with patch("main_assist.CONFIG_PATH", path):
                config = app._load_config()

            self.assertEqual(config["attack_trigger_key"], "F8")
            self.assertEqual(config["follow_trigger_key"], MAIN_DEFAULTS["follow_trigger_key"])
            self.assertNotIn("trigger_key", config)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("trigger_key", saved)

    def test_box_v1_output_is_migrated_to_attack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "box_settings.json"
            path.write_text(
                json.dumps(
                    {
                        "box_name": "Old Box",
                        "main_host": "AUTO",
                        "port": 45880,
                        "discovery_port": 45881,
                        "pairing_key": "OLD-PAIRING",
                        "output_key": "F7",
                        "target_process": "L2.exe",
                    }
                ),
                encoding="utf-8",
            )
            app = BoxAssistApp.__new__(BoxAssistApp)
            with patch("box_assist.CONFIG_PATH", path):
                config = app._load_config()

            self.assertEqual(config["attack_output_key"], "F7")
            self.assertEqual(config["follow_output_key"], BOX_DEFAULTS["follow_output_key"])
            self.assertNotIn("output_key", config)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("output_key", saved)


class LogitechWrapperTests(unittest.TestCase):
    def test_key_down_and_key_up_order_for_a_chord(self) -> None:
        driver = LogitechInput()
        events: list[tuple[int, bool]] = []

        with patch.object(driver, "initialize", return_value=True), patch.object(
            driver,
            "_send_event",
            side_effect=lambda vk_code, *, key_up: events.append((vk_code, key_up)),
        ), patch("logitech_input.time.sleep", return_value=None):
            result = driver.tap("CTRL+F2", hold_ms=45)

        self.assertTrue(result.ok)
        self.assertEqual(
            events,
            [
                (0x11, False),
                (0x71, False),
                (0x71, True),
                (0x11, True),
            ],
        )


class NetworkIntegrationTests(unittest.TestCase):
    def _wait_for_matching(
        self,
        events: queue.Queue[dict],
        predicate,
        *,
        timeout: float = 5.0,
    ) -> dict:
        deadline = time.monotonic() + timeout
        received: list[str] = []
        while time.monotonic() < deadline:
            try:
                event = events.get(timeout=0.1)
            except queue.Empty:
                continue
            received.append(str(event.get("type")))
            if predicate(event):
                return event
        self.fail(f"Expected event not received. Seen types: {received}")

    def _wait_for_event(self, events: queue.Queue[dict], event_type: str, timeout: float = 5.0) -> dict:
        return self._wait_for_matching(events, lambda event: event.get("type") == event_type, timeout=timeout)

    @staticmethod
    def _make_client(
        *,
        server: MainAssistServer,
        client_id: str,
        box_name: str,
        action_handler,
        event_callback=None,
        pairing_key: str = "TEST-PAIRING",
    ) -> BoxAssistClient:
        return BoxAssistClient(
            client_id=client_id,
            box_name=box_name,
            action_keys={ACTION_ATTACK: "&", ACTION_FOLLOW: "VK_2"},
            main_host="127.0.0.1",
            port=server.port,
            discovery_port=0,
            pairing_key=pairing_key,
            reconnect_delay=0.5,
            action_handler=action_handler,
            event_callback=event_callback,
        )

    def test_attack_and_follow_round_trip(self) -> None:
        server_events: queue.Queue[dict] = queue.Queue()
        client_events: queue.Queue[dict] = queue.Queue()
        received_actions: queue.Queue[tuple[str, int]] = queue.Queue()

        server = MainAssistServer(
            host="127.0.0.1",
            port=0,
            discovery_port=0,
            pairing_key="TEST-PAIRING",
            main_name="Test Main",
            event_callback=server_events.put,
        )
        server.start()

        def action_handler(message: dict) -> tuple[bool, str]:
            received_actions.put((str(message.get("action")), int(message.get("sequence", 0))))
            return True, f"mock {message.get('action')} sent"

        client = self._make_client(
            server=server,
            client_id="box-test-123456",
            box_name="Test Box",
            action_handler=action_handler,
            event_callback=client_events.put,
        )
        client.start()

        try:
            connected = self._wait_for_event(client_events, "connected")
            self.assertEqual(connected["main_name"], "Test Main")
            peers = server.peer_snapshot()
            self.assertEqual(len(peers), 1)
            self.assertEqual(peers[0]["action_keys"][ACTION_ATTACK], "&")
            self.assertEqual(peers[0]["action_keys"][ACTION_FOLLOW], "VK_2")

            for expected_action, trigger in ((ACTION_ATTACK, "F2"), (ACTION_FOLLOW, "F3")):
                event_id, sent = server.broadcast_action(
                    action=expected_action,
                    trigger=trigger,
                    source="test",
                )
                self.assertEqual(sent, 1)
                received_action, sequence = received_actions.get(timeout=3.0)
                self.assertEqual(received_action, expected_action)
                self.assertGreater(sequence, 0)

                ack = self._wait_for_matching(
                    server_events,
                    lambda event, eid=event_id: event.get("type") == "ack" and event.get("event_id") == eid,
                )
                self.assertEqual(ack["action"], expected_action)
                self.assertTrue(ack["ok"])
                self.assertEqual(ack["detail"], f"mock {expected_action} sent")
        finally:
            client.stop()
            server.stop()

    def test_two_boxes_receive_follow(self) -> None:
        server_events: queue.Queue[dict] = queue.Queue()
        called_one = threading.Event()
        called_two = threading.Event()
        actions: queue.Queue[str] = queue.Queue()

        server = MainAssistServer(
            host="127.0.0.1",
            port=0,
            discovery_port=0,
            pairing_key="TEST-PAIRING",
            main_name="Test Main",
            event_callback=server_events.put,
        )
        server.start()

        def make_handler(name: str, called: threading.Event):
            def action_handler(message: dict) -> tuple[bool, str]:
                actions.put(str(message.get("action")))
                called.set()
                return True, f"{name} sent"

            return action_handler

        client_one = self._make_client(
            server=server,
            client_id="box-test-one",
            box_name="Box One",
            action_handler=make_handler("Box One", called_one),
        )
        client_two = self._make_client(
            server=server,
            client_id="box-test-two",
            box_name="Box Two",
            action_handler=make_handler("Box Two", called_two),
        )
        client_one.start()
        client_two.start()

        try:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and len(server.peer_snapshot()) < 2:
                time.sleep(0.02)
            self.assertEqual(len(server.peer_snapshot()), 2)

            event_id, sent = server.broadcast_action(action=ACTION_FOLLOW, trigger="F3", source="test")
            self.assertEqual(sent, 2)
            self.assertTrue(called_one.wait(3.0))
            self.assertTrue(called_two.wait(3.0))
            self.assertEqual([actions.get(timeout=1.0), actions.get(timeout=1.0)], [ACTION_FOLLOW, ACTION_FOLLOW])

            matching_acks = 0
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and matching_acks < 2:
                try:
                    event = server_events.get(timeout=0.1)
                except queue.Empty:
                    continue
                if event.get("type") == "ack" and event.get("event_id") == event_id:
                    self.assertEqual(event.get("action"), ACTION_FOLLOW)
                    matching_acks += 1
            self.assertEqual(matching_acks, 2)
        finally:
            client_one.stop()
            client_two.stop()
            server.stop()

    def test_concurrent_broadcasts_keep_sequence_order(self) -> None:
        received_sequences: queue.Queue[int] = queue.Queue()
        server = MainAssistServer(
            host="127.0.0.1",
            port=0,
            discovery_port=0,
            pairing_key="TEST-PAIRING",
            main_name="Test Main",
        )
        server.start()

        def action_handler(message: dict) -> tuple[bool, str]:
            received_sequences.put(int(message["sequence"]))
            return True, "ok"

        client = self._make_client(
            server=server,
            client_id="box-sequence-test",
            box_name="Sequence Box",
            action_handler=action_handler,
        )
        client.start()

        try:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and len(server.peer_snapshot()) < 1:
                time.sleep(0.02)
            self.assertEqual(len(server.peer_snapshot()), 1)

            threads: list[threading.Thread] = []
            for index in range(20):
                action = ACTION_ATTACK if index % 2 == 0 else ACTION_FOLLOW
                thread = threading.Thread(
                    target=server.broadcast_action,
                    kwargs={"action": action, "trigger": "TEST", "source": "concurrent"},
                )
                threads.append(thread)
                thread.start()
            for thread in threads:
                thread.join(timeout=3.0)
                self.assertFalse(thread.is_alive())

            sequences = [received_sequences.get(timeout=5.0) for _ in range(20)]
            self.assertEqual(sequences, list(range(1, 21)))
        finally:
            client.stop()
            server.stop()

    def test_unknown_action_is_rejected_before_network_send(self) -> None:
        server = MainAssistServer(
            host="127.0.0.1",
            port=0,
            discovery_port=0,
            pairing_key="TEST-PAIRING",
            main_name="Test Main",
        )
        server.start()
        try:
            with self.assertRaises(ValueError):
                server.broadcast_action(action="dance", trigger="F4")
        finally:
            server.stop()

    def test_unicode_pairing_key_connects(self) -> None:
        client_events: queue.Queue[dict] = queue.Queue()
        server = MainAssistServer(
            host="127.0.0.1",
            port=0,
            discovery_port=0,
            pairing_key="clé-été-2026",
            main_name="Unicode Main",
        )
        server.start()
        client = self._make_client(
            server=server,
            client_id="box-unicode-key",
            box_name="Unicode Box",
            action_handler=lambda _message: (True, "ok"),
            event_callback=client_events.put,
            pairing_key="clé-été-2026",
        )
        client.start()
        try:
            connected = self._wait_for_event(client_events, "connected")
            self.assertEqual(connected["main_name"], "Unicode Main")
            self.assertEqual(len(server.peer_snapshot()), 1)
        finally:
            client.stop()
            server.stop()

    def test_wrong_pairing_key_is_rejected(self) -> None:
        server_events: queue.Queue[dict] = queue.Queue()
        client_events: queue.Queue[dict] = queue.Queue()
        handler_called = threading.Event()
        server = MainAssistServer(
            host="127.0.0.1",
            port=0,
            discovery_port=0,
            pairing_key="CORRECT-KEY",
            main_name="Test Main",
            event_callback=server_events.put,
        )
        server.start()

        def action_handler(_message: dict) -> tuple[bool, str]:
            handler_called.set()
            return True, "unexpected"

        client = self._make_client(
            server=server,
            client_id="box-wrong-key",
            box_name="Wrong Key Box",
            action_handler=action_handler,
            event_callback=client_events.put,
            pairing_key="WRONG-KEY",
        )
        client.start()
        try:
            auth_failed = self._wait_for_event(server_events, "auth_failed")
            self.assertEqual(auth_failed["ip"], "127.0.0.1")
            self.assertEqual(server.peer_snapshot(), [])
            self.assertFalse(handler_called.is_set())
        finally:
            client.stop()
            server.stop()

    def test_box_reconnects_after_main_restart(self) -> None:
        client_events: queue.Queue[dict] = queue.Queue()
        received: queue.Queue[str] = queue.Queue()
        server_one = MainAssistServer(
            host="127.0.0.1",
            port=0,
            discovery_port=0,
            pairing_key="TEST-PAIRING",
            main_name="Main One",
        )
        server_one.start()
        port = server_one.port

        def action_handler(message: dict) -> tuple[bool, str]:
            received.put(str(message.get("action")))
            return True, "ok"

        client = self._make_client(
            server=server_one,
            client_id="box-reconnect-test",
            box_name="Reconnect Box",
            action_handler=action_handler,
            event_callback=client_events.put,
        )
        client.start()
        server_two: MainAssistServer | None = None
        try:
            first_connection = self._wait_for_event(client_events, "connected")
            self.assertEqual(first_connection["main_name"], "Main One")

            server_one.stop()
            self._wait_for_event(client_events, "disconnected")

            server_two = MainAssistServer(
                host="127.0.0.1",
                port=port,
                discovery_port=0,
                pairing_key="TEST-PAIRING",
                main_name="Main Two",
            )
            server_two.start()
            second_connection = self._wait_for_event(client_events, "connected", timeout=8.0)
            self.assertEqual(second_connection["main_name"], "Main Two")

            _, sent = server_two.broadcast_action(action=ACTION_FOLLOW, trigger="F3", source="test")
            self.assertEqual(sent, 1)
            self.assertEqual(received.get(timeout=3.0), ACTION_FOLLOW)
        finally:
            client.stop()
            if server_two:
                server_two.stop()
            else:
                server_one.stop()


if __name__ == "__main__":
    unittest.main()

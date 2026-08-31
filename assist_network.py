from __future__ import annotations

import hmac
import json
import re
import select
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from assist_common import (
    ACTION_IDS,
    MAX_MESSAGE_BYTES,
    PROTOCOL_VERSION,
    action_label,
    clean_text,
    extract_json_lines,
    normalize_action,
    send_json_line,
    utc_timestamp,
)


DISCOVERY_MAGIC = b"L2_SIMPLE_ASSIST_DISCOVER_V2"
EventCallback = Callable[[dict[str, Any]], None]
ActionHandler = Callable[[dict[str, Any]], tuple[bool, str]]


def _safe_emit(callback: EventCallback | None, event_type: str, **payload: Any) -> None:
    if not callback:
        return
    event = {"type": event_type, "time": utc_timestamp(), **payload}
    try:
        callback(event)
    except Exception:
        pass


def _close_socket(sock: socket.socket | None) -> None:
    if not sock:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def _valid_client_id(value: Any) -> str:
    text = clean_text(value, max_length=64)
    if re.fullmatch(r"[A-Za-z0-9_.-]{6,64}", text):
        return text
    return uuid.uuid4().hex


@dataclass
class PeerConnection:
    client_id: str
    name: str
    address: tuple[str, int]
    action_keys: dict[str, str]
    connected_at: float
    last_seen: float
    sock: socket.socket
    send_lock: threading.Lock = field(default_factory=threading.Lock)
    last_result: str = "En attente"

    def public(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "name": self.name,
            "ip": self.address[0],
            "port": self.address[1],
            "action_keys": dict(self.action_keys),
            "connected_at": self.connected_at,
            "last_seen": self.last_seen,
            "last_result": self.last_result,
        }


class MainAssistServer:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        discovery_port: int,
        pairing_key: str,
        main_name: str,
        event_callback: EventCallback | None = None,
    ) -> None:
        self.host = host
        self.requested_port = port
        self.port = port
        self.discovery_port = discovery_port
        self.pairing_key = pairing_key
        self.main_name = clean_text(main_name, max_length=60, fallback="Main")
        self.event_callback = event_callback

        self._stop_event = threading.Event()
        self._listen_socket: socket.socket | None = None
        self._discovery_socket: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._discovery_thread: threading.Thread | None = None
        self._client_threads: set[threading.Thread] = set()
        self._client_threads_lock = threading.Lock()
        self._peers: dict[str, PeerConnection] = {}
        self._peers_lock = threading.RLock()
        self._broadcast_lock = threading.Lock()
        self._peer_list_broadcast_lock = threading.Lock()
        self._sequence = 0
        self._running = False

    @property
    def running(self) -> bool:
        return self._running and not self._stop_event.is_set()

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()

        listen_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listen_socket.bind((self.host, self.requested_port))
            listen_socket.listen(16)
            listen_socket.settimeout(1.0)
        except Exception:
            _close_socket(listen_socket)
            raise
        self.port = int(listen_socket.getsockname()[1])
        self._listen_socket = listen_socket
        self._running = True

        self._accept_thread = threading.Thread(target=self._accept_loop, name="MainAssist-Accept", daemon=True)
        self._accept_thread.start()

        if self.discovery_port > 0:
            self._discovery_thread = threading.Thread(
                target=self._discovery_loop,
                name="MainAssist-Discovery",
                daemon=True,
            )
            self._discovery_thread.start()

        _safe_emit(
            self.event_callback,
            "server_started",
            host=self.host,
            port=self.port,
            discovery_port=self.discovery_port,
        )

    def stop(self) -> None:
        if not self._running:
            return
        self._stop_event.set()
        _close_socket(self._listen_socket)
        _close_socket(self._discovery_socket)
        self._listen_socket = None
        self._discovery_socket = None

        with self._peers_lock:
            peers = list(self._peers.values())
            self._peers.clear()
        for peer in peers:
            _close_socket(peer.sock)

        current = threading.current_thread()
        for thread in (self._accept_thread, self._discovery_thread):
            if thread and thread is not current and thread.is_alive():
                thread.join(timeout=1.5)
        with self._client_threads_lock:
            client_threads = list(self._client_threads)
        for thread in client_threads:
            if thread is not current and thread.is_alive():
                thread.join(timeout=1.5)

        self._accept_thread = None
        self._discovery_thread = None

        self._running = False
        _safe_emit(self.event_callback, "peers", boxes=[])
        _safe_emit(self.event_callback, "server_stopped")

    def peer_snapshot(self) -> list[dict[str, Any]]:
        with self._peers_lock:
            peers = [peer.public() for peer in self._peers.values()]
        return sorted(peers, key=lambda peer: (str(peer["name"]).lower(), str(peer["ip"])))

    def _accept_loop(self) -> None:
        while not self._stop_event.is_set():
            listen_socket = self._listen_socket
            if not listen_socket:
                break
            try:
                client_socket, address = listen_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client_socket.settimeout(3.0)
            worker = threading.Thread(
                target=self._client_thread_entry,
                args=(client_socket, (str(address[0]), int(address[1]))),
                name=f"MainAssist-Client-{address[0]}",
                daemon=True,
            )
            with self._client_threads_lock:
                self._client_threads.add(worker)
            worker.start()

    def _client_thread_entry(self, sock: socket.socket, address: tuple[str, int]) -> None:
        try:
            self._client_loop(sock, address)
        finally:
            with self._client_threads_lock:
                self._client_threads.discard(threading.current_thread())

    def _receive_available(
        self,
        sock: socket.socket,
        buffer: bytes,
        timeout: float,
    ) -> tuple[list[dict[str, Any]], bytes, bool]:
        try:
            readable, _, _ = select.select([sock], [], [], timeout)
        except (OSError, ValueError):
            return [], buffer, False
        if not readable:
            return [], buffer, True
        try:
            chunk = sock.recv(4096)
        except OSError:
            return [], buffer, False
        if not chunk:
            return [], buffer, False
        if len(buffer) + len(chunk) > MAX_MESSAGE_BYTES * 2:
            raise ValueError("Tampon réseau trop volumineux.")
        messages, remainder = extract_json_lines(buffer + chunk)
        return messages, remainder, True

    def _wait_for_hello(self, sock: socket.socket) -> tuple[dict[str, Any] | None, list[dict[str, Any]], bytes]:
        deadline = time.monotonic() + 10.0
        buffer = b""
        pending: list[dict[str, Any]] = []
        while not self._stop_event.is_set() and time.monotonic() < deadline:
            messages, buffer, alive = self._receive_available(sock, buffer, 0.5)
            if not alive:
                return None, [], buffer
            if messages:
                hello = messages[0]
                pending.extend(messages[1:])
                return hello, pending, buffer
        return None, [], buffer

    def _client_loop(self, sock: socket.socket, address: tuple[str, int]) -> None:
        peer: PeerConnection | None = None
        client_id = ""
        try:
            hello, pending, buffer = self._wait_for_hello(sock)
            if not hello:
                send_json_line(sock, threading.Lock(), {"type": "error", "message": "Handshake expiré."})
                return

            if hello.get("type") != "hello" or int(hello.get("protocol", -1)) != PROTOCOL_VERSION:
                send_json_line(sock, threading.Lock(), {"type": "error", "message": "Protocole incompatible."})
                return

            provided_key = str(hello.get("pairing_key", ""))
            if not hmac.compare_digest(
                provided_key.encode("utf-8", errors="surrogatepass"),
                self.pairing_key.encode("utf-8", errors="surrogatepass"),
            ):
                _safe_emit(self.event_callback, "auth_failed", ip=address[0])
                send_json_line(sock, threading.Lock(), {"type": "error", "message": "Clé d'appairage incorrecte."})
                return

            client_id = _valid_client_id(hello.get("client_id"))
            name = clean_text(hello.get("name"), max_length=60, fallback=f"Box {address[0]}")
            raw_action_keys = hello.get("action_keys")
            if not isinstance(raw_action_keys, dict):
                send_json_line(
                    sock,
                    threading.Lock(),
                    {"type": "error", "message": "Configuration des actions absente ou invalide."},
                )
                return
            action_keys = {
                action: clean_text(raw_action_keys.get(action), max_length=32, fallback="?")
                for action in ACTION_IDS
            }
            now = utc_timestamp()
            peer = PeerConnection(
                client_id=client_id,
                name=name,
                address=address,
                action_keys=action_keys,
                connected_at=now,
                last_seen=now,
                sock=sock,
            )

            with self._peers_lock:
                welcome_boxes = [
                    existing.public()
                    for existing_id, existing in self._peers.items()
                    if existing_id != client_id
                ]
            welcome_boxes.append(peer.public())
            welcome_boxes.sort(key=lambda item: (str(item["name"]).lower(), str(item["ip"])))

            welcome = {
                "type": "welcome",
                "protocol": PROTOCOL_VERSION,
                "main_name": self.main_name,
                "server_time": utc_timestamp(),
                "boxes": welcome_boxes,
            }
            if not send_json_line(sock, peer.send_lock, welcome):
                return

            previous: PeerConnection | None = None
            with self._peers_lock:
                previous = self._peers.get(client_id)
                self._peers[client_id] = peer
            if previous and previous is not peer:
                _close_socket(previous.sock)

            _safe_emit(
                self.event_callback,
                "peer_connected",
                client_id=client_id,
                name=name,
                ip=address[0],
                action_keys=dict(action_keys),
            )
            self._broadcast_peer_list()

            last_received = time.monotonic()
            messages = pending
            while not self._stop_event.is_set():
                if not messages:
                    messages, buffer, alive = self._receive_available(sock, buffer, 0.75)
                    if not alive:
                        break
                if messages:
                    last_received = time.monotonic()

                for message in messages:
                    message_type = str(message.get("type", "")).lower()
                    peer.last_seen = utc_timestamp()

                    if message_type == "ping":
                        if not send_json_line(sock, peer.send_lock, {"type": "pong", "time": utc_timestamp()}):
                            return
                    elif message_type == "ack":
                        ok = bool(message.get("ok"))
                        detail = clean_text(message.get("detail"), max_length=180, fallback="Sans détail")
                        event_id = clean_text(message.get("event_id"), max_length=64)
                        action = normalize_action(message.get("action"))
                        label = action_label(action)
                        peer.last_result = f"{label}: " + (("OK: " if ok else "ERREUR: ") + detail)
                        _safe_emit(
                            self.event_callback,
                            "ack",
                            client_id=peer.client_id,
                            name=peer.name,
                            ip=peer.address[0],
                            event_id=event_id,
                            action=action,
                            ok=ok,
                            detail=detail,
                        )
                        self._broadcast_peer_list()
                    elif message_type == "goodbye":
                        return
                messages = []

                if time.monotonic() - last_received > 35.0:
                    _safe_emit(
                        self.event_callback,
                        "peer_timeout",
                        client_id=peer.client_id,
                        name=peer.name,
                        ip=peer.address[0],
                    )
                    break
        except (OSError, ValueError, TypeError) as exc:
            _safe_emit(self.event_callback, "network_error", side="server", ip=address[0], message=str(exc))
        finally:
            removed = False
            if peer and client_id:
                with self._peers_lock:
                    if self._peers.get(client_id) is peer:
                        del self._peers[client_id]
                        removed = True
            _close_socket(sock)
            if removed and peer:
                _safe_emit(
                    self.event_callback,
                    "peer_disconnected",
                    client_id=peer.client_id,
                    name=peer.name,
                    ip=peer.address[0],
                )
                self._broadcast_peer_list()

    def _broadcast_peer_list(self) -> None:
        # Snapshot and delivery are serialized so a slower, older snapshot
        # cannot arrive after a newer one in another client thread.
        with self._peer_list_broadcast_lock:
            boxes = self.peer_snapshot()
            payload = {"type": "peers", "boxes": boxes, "server_time": utc_timestamp()}
            with self._peers_lock:
                peers = list(self._peers.values())
            for peer in peers:
                if not send_json_line(peer.sock, peer.send_lock, payload):
                    _close_socket(peer.sock)
            _safe_emit(self.event_callback, "peers", boxes=boxes)

    def broadcast_action(self, *, action: str, trigger: str, source: str = "hotkey") -> tuple[str, int]:
        if not self.running:
            raise RuntimeError("Le serveur Main n'est pas démarré.")
        normalized_action = normalize_action(action)
        if not normalized_action:
            raise ValueError(f"Action inconnue: {action!r}")

        # The lock keeps command order identical for every Box, even if a UI
        # test button and a physical hotkey fire at almost the same moment.
        with self._broadcast_lock:
            self._sequence += 1
            event_id = uuid.uuid4().hex
            payload = {
                "type": "action",
                "protocol": PROTOCOL_VERSION,
                "event_id": event_id,
                "sequence": self._sequence,
                "action": normalized_action,
                "trigger": clean_text(trigger, max_length=32, fallback="?"),
                "source": clean_text(source, max_length=24, fallback="hotkey"),
                "sent_at": utc_timestamp(),
            }
            with self._peers_lock:
                peers = list(self._peers.values())

            sent = 0
            for peer in peers:
                if send_json_line(peer.sock, peer.send_lock, payload):
                    sent += 1
                else:
                    _close_socket(peer.sock)

        _safe_emit(
            self.event_callback,
            "broadcast",
            event_id=event_id,
            action=normalized_action,
            trigger=payload["trigger"],
            source=payload["source"],
            sent=sent,
            connected=len(peers),
        )
        return event_id, sent

    def _discovery_loop(self) -> None:
        discovery_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._discovery_socket = discovery_socket
        try:
            discovery_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            discovery_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            discovery_socket.bind(("", self.discovery_port))
            discovery_socket.settimeout(1.0)
            _safe_emit(self.event_callback, "discovery_started", port=self.discovery_port)

            while not self._stop_event.is_set():
                try:
                    data, address = discovery_socket.recvfrom(2048)
                except socket.timeout:
                    continue
                except OSError:
                    break

                valid = data.strip() == DISCOVERY_MAGIC
                if not valid:
                    try:
                        payload = json.loads(data.decode("utf-8"))
                        valid = (
                            isinstance(payload, dict)
                            and payload.get("type") == "discover"
                            and int(payload.get("protocol", -1)) == PROTOCOL_VERSION
                        )
                    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                        valid = False
                if not valid:
                    continue

                response = json.dumps(
                    {
                        "type": "discover_reply",
                        "protocol": PROTOCOL_VERSION,
                        "main_name": self.main_name,
                        "port": self.port,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                try:
                    discovery_socket.sendto(response, address)
                except OSError:
                    continue
        except OSError as exc:
            _safe_emit(
                self.event_callback,
                "discovery_error",
                port=self.discovery_port,
                message=str(exc),
            )
        finally:
            _close_socket(discovery_socket)
            if self._discovery_socket is discovery_socket:
                self._discovery_socket = None


def discover_main(discovery_port: int, *, timeout: float = 1.5) -> dict[str, Any] | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", 0))
        sock.setblocking(False)

        destinations = [("255.255.255.255", discovery_port), ("<broadcast>", discovery_port)]
        for destination in destinations:
            try:
                sock.sendto(DISCOVERY_MAGIC, destination)
            except OSError:
                pass

        deadline = time.monotonic() + max(0.2, timeout)
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                readable, _, _ = select.select([sock], [], [], min(0.25, remaining))
            except (OSError, ValueError):
                return None
            if not readable:
                continue
            try:
                data, address = sock.recvfrom(4096)
                payload = json.loads(data.decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or payload.get("type") != "discover_reply":
                continue
            try:
                protocol = int(payload.get("protocol", -1))
                port = int(payload.get("port", 0))
            except (TypeError, ValueError):
                continue
            if protocol == PROTOCOL_VERSION and 1 <= port <= 65535:
                return {
                    "host": str(address[0]),
                    "port": port,
                    "main_name": clean_text(payload.get("main_name"), max_length=60, fallback="Main"),
                }
        return None
    finally:
        _close_socket(sock)


class BoxAssistClient:
    def __init__(
        self,
        *,
        client_id: str,
        box_name: str,
        action_keys: dict[str, str],
        main_host: str,
        port: int,
        discovery_port: int,
        pairing_key: str,
        reconnect_delay: float,
        action_handler: ActionHandler,
        event_callback: EventCallback | None = None,
    ) -> None:
        self.client_id = _valid_client_id(client_id)
        self.box_name = clean_text(box_name, max_length=60, fallback="Box")
        self.action_keys = {
            action: clean_text(action_keys.get(action), max_length=32, fallback="?")
            for action in ACTION_IDS
        }
        self.main_host = clean_text(main_host, max_length=255, fallback="AUTO")
        self.port = int(port)
        self.discovery_port = int(discovery_port)
        self.pairing_key = pairing_key
        self.reconnect_delay = max(0.5, min(float(reconnect_delay), 30.0))
        self.action_handler = action_handler
        self.event_callback = event_callback

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._socket_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected and not self._stop_event.is_set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="BoxAssist-Client", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        sock = self._current_socket()
        if sock:
            # Closing directly is deliberate: a best-effort goodbye could block
            # the Tkinter thread for the full socket timeout on a dead network.
            _close_socket(sock)
        with self._socket_lock:
            self._socket = None
        self._connected = False
        thread = self._thread
        if thread and thread is not threading.current_thread() and thread.is_alive():
            thread.join(timeout=2.0)
        if thread and not thread.is_alive():
            self._thread = None

    def _current_socket(self) -> socket.socket | None:
        with self._socket_lock:
            return self._socket

    def _set_socket(self, sock: socket.socket | None) -> None:
        with self._socket_lock:
            self._socket = sock

    def _sleep_reconnect(self) -> None:
        deadline = time.monotonic() + self.reconnect_delay
        while not self._stop_event.is_set() and time.monotonic() < deadline:
            time.sleep(0.1)

    def _resolve_host(self) -> tuple[str, int] | None:
        if self.main_host.upper() != "AUTO":
            return self.main_host, self.port

        _safe_emit(self.event_callback, "discovering", port=self.discovery_port)
        found = discover_main(self.discovery_port)
        if not found:
            _safe_emit(self.event_callback, "discovery_failed", port=self.discovery_port)
            return None
        host = str(found["host"])
        port = int(found.get("port") or self.port)
        _safe_emit(
            self.event_callback,
            "discovered",
            host=host,
            port=port,
            main_name=found.get("main_name", "Main"),
        )
        return host, port

    def _run(self) -> None:
        while not self._stop_event.is_set():
            endpoint = self._resolve_host()
            if not endpoint:
                self._sleep_reconnect()
                continue
            host, port = endpoint
            _safe_emit(self.event_callback, "connecting", host=host, port=port)

            sock: socket.socket | None = None
            disconnect_reason = "Connexion fermée."
            try:
                sock = socket.create_connection((host, port), timeout=5.0)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.settimeout(3.0)
                if self._stop_event.is_set():
                    _close_socket(sock)
                    break
                self._set_socket(sock)

                hello = {
                    "type": "hello",
                    "protocol": PROTOCOL_VERSION,
                    "client_id": self.client_id,
                    "name": self.box_name,
                    "action_keys": dict(self.action_keys),
                    "pairing_key": self.pairing_key,
                }
                if not send_json_line(sock, self._send_lock, hello):
                    raise ConnectionError("Impossible d'envoyer le handshake.")

                buffer = b""
                welcomed = False
                welcome_deadline = time.monotonic() + 10.0
                last_ping = 0.0

                while not self._stop_event.is_set():
                    now = time.monotonic()
                    try:
                        readable, _, _ = select.select([sock], [], [], 0.5)
                    except (OSError, ValueError) as exc:
                        raise ConnectionError(str(exc)) from exc

                    messages: list[dict[str, Any]] = []
                    if readable:
                        try:
                            chunk = sock.recv(4096)
                        except OSError as exc:
                            raise ConnectionError(str(exc)) from exc
                        if not chunk:
                            raise ConnectionError("Le Main a fermé la connexion.")
                        if len(buffer) + len(chunk) > MAX_MESSAGE_BYTES * 2:
                            raise ConnectionError("Tampon réseau trop volumineux.")
                        messages, buffer = extract_json_lines(buffer + chunk)

                    for message in messages:
                        message_type = str(message.get("type", "")).lower()
                        if message_type == "welcome":
                            try:
                                welcome_protocol = int(message.get("protocol", -1))
                            except (TypeError, ValueError) as exc:
                                raise ConnectionError("Réponse de bienvenue invalide.") from exc
                            if welcome_protocol != PROTOCOL_VERSION:
                                raise ConnectionError("Protocole du Main incompatible.")
                            welcomed = True
                            self._connected = True
                            _safe_emit(
                                self.event_callback,
                                "connected",
                                host=host,
                                port=port,
                                main_name=clean_text(message.get("main_name"), max_length=60, fallback="Main"),
                                boxes=message.get("boxes", []),
                            )
                        elif message_type == "peers":
                            _safe_emit(self.event_callback, "peers", boxes=message.get("boxes", []))
                        elif message_type == "action":
                            if not welcomed:
                                raise ConnectionError("Commande reçue avant la validation du Main.")
                            event_id = clean_text(message.get("event_id"), max_length=64)
                            action = normalize_action(message.get("action"))
                            _safe_emit(
                                self.event_callback,
                                "action_received",
                                event_id=event_id,
                                action=action,
                                trigger=clean_text(message.get("trigger"), max_length=32, fallback="?"),
                                sequence=message.get("sequence", 0),
                            )
                            if not action:
                                ok, detail = False, "Action inconnue reçue du Main."
                            else:
                                try:
                                    ok, detail = self.action_handler(message)
                                except Exception as exc:
                                    ok, detail = False, f"Erreur interne pendant l'injection: {exc}"
                            detail = clean_text(detail, max_length=180, fallback="Sans détail")
                            if not send_json_line(
                                sock,
                                self._send_lock,
                                {
                                    "type": "ack",
                                    "event_id": event_id,
                                    "action": action,
                                    "ok": bool(ok),
                                    "detail": detail,
                                    "time": utc_timestamp(),
                                },
                            ):
                                raise ConnectionError("Impossible d'envoyer l'accusé de réception au Main.")
                            _safe_emit(
                                self.event_callback,
                                "action_result",
                                event_id=event_id,
                                action=action,
                                ok=bool(ok),
                                detail=detail,
                            )
                        elif message_type == "pong":
                            pass
                        elif message_type == "error":
                            disconnect_reason = clean_text(
                                message.get("message"),
                                max_length=180,
                                fallback="Erreur renvoyée par le Main.",
                            )
                            _safe_emit(self.event_callback, "server_error", message=disconnect_reason)
                            raise ConnectionError(disconnect_reason)

                    if not welcomed and time.monotonic() > welcome_deadline:
                        raise ConnectionError("Le Main n'a pas validé la connexion.")

                    if now - last_ping >= 5.0:
                        if not send_json_line(sock, self._send_lock, {"type": "ping", "time": utc_timestamp()}):
                            raise ConnectionError("Le heartbeat réseau a échoué.")
                        last_ping = now
            except (OSError, ValueError, TypeError, ConnectionError) as exc:
                disconnect_reason = str(exc) or disconnect_reason
                if not self._stop_event.is_set():
                    _safe_emit(
                        self.event_callback,
                        "connection_error",
                        host=host,
                        port=port,
                        message=disconnect_reason,
                    )
            finally:
                self._connected = False
                if sock:
                    _close_socket(sock)
                if self._current_socket() is sock:
                    self._set_socket(None)
                _safe_emit(
                    self.event_callback,
                    "disconnected",
                    host=host,
                    port=port,
                    reason=disconnect_reason,
                )

            if not self._stop_event.is_set():
                self._sleep_reconnect()

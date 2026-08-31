from __future__ import annotations

import ctypes
import json
import os
import re
import socket
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    from ctypes import wintypes
except ImportError:  # pragma: no cover - ctypes always ships with CPython
    wintypes = None  # type: ignore[assignment]


PROTOCOL_VERSION = 2
DEFAULT_PORT = 45880
DEFAULT_DISCOVERY_PORT = 45881
MAX_MESSAGE_BYTES = 16 * 1024
APP_DIR = Path(__file__).resolve().parent

ACTION_ATTACK = "attack"
ACTION_FOLLOW = "follow"
ACTION_LABELS: dict[str, str] = {
    ACTION_ATTACK: "Attaquer",
    ACTION_FOLLOW: "Suivre",
}
ACTION_IDS = tuple(ACTION_LABELS)


class ConfigError(ValueError):
    pass


def deep_merge(defaults: dict[str, Any], loaded: dict[str, Any]) -> dict[str, Any]:
    result = dict(defaults)
    for key, value in loaded.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_json_config(path: Path, defaults: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        config = dict(defaults)
        save_json_config(path, config)
        return config

    try:
        with path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Impossible de lire {path.name}: {exc}") from exc

    if not isinstance(loaded, dict):
        raise ConfigError(f"{path.name} doit contenir un objet JSON.")
    return deep_merge(defaults, loaded)


def save_json_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as handle:
            handle.write(payload)
            temp_name = handle.name
        os.replace(temp_name, path)
        temp_name = None
    except OSError as exc:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise ConfigError(f"Impossible d'enregistrer {path.name}: {exc}") from exc


def clean_text(value: Any, *, max_length: int = 80, fallback: str = "") -> str:
    text = str(value or "").strip()
    text = "".join(ch for ch in text if ch >= " " and ch not in "\r\n")
    return (text[:max_length] or fallback).strip()


def normalize_action(value: Any) -> str:
    action = clean_text(value, max_length=24).lower()
    return action if action in ACTION_LABELS else ""


def action_label(value: Any) -> str:
    action = normalize_action(value)
    return ACTION_LABELS.get(action, "Action inconnue")


def utc_timestamp() -> float:
    return time.time()


def format_clock(timestamp: float | None = None) -> str:
    return time.strftime("%H:%M:%S", time.localtime(timestamp or time.time()))


def send_json_line(sock: socket.socket, lock: threading.Lock, payload: dict[str, Any]) -> bool:
    try:
        encoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    except (TypeError, ValueError):
        return False
    if len(encoded) > MAX_MESSAGE_BYTES:
        return False

    try:
        with lock:
            sock.sendall(encoded)
        return True
    except OSError:
        return False


def extract_json_lines(buffer: bytes) -> tuple[list[dict[str, Any]], bytes]:
    messages: list[dict[str, Any]] = []
    while b"\n" in buffer:
        raw_line, buffer = buffer.split(b"\n", 1)
        if not raw_line.strip():
            continue
        if len(raw_line) > MAX_MESSAGE_BYTES:
            raise ValueError("Message réseau trop volumineux.")
        try:
            decoded = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Message réseau JSON invalide.") from exc
        if not isinstance(decoded, dict):
            raise ValueError("Le message réseau doit être un objet JSON.")
        messages.append(decoded)
    if len(buffer) > MAX_MESSAGE_BYTES:
        raise ValueError("Tampon réseau trop volumineux.")
    return messages, buffer


def local_ipv4_addresses() -> list[str]:
    addresses: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                addresses.add(ip)
    except OSError:
        pass

    # This does not send traffic. It only asks Windows which local interface
    # would be selected for an external IPv4 route.
    probe: socket.socket | None = None
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 9))
        ip = probe.getsockname()[0]
        if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
            addresses.add(ip)
    except OSError:
        pass
    finally:
        if probe:
            probe.close()

    return sorted(addresses)


def parse_target_processes(value: str | Iterable[str]) -> set[str]:
    if isinstance(value, str):
        parts = re.split(r"[,;]", value)
    else:
        parts = list(value)
    return {Path(str(part).strip()).name.lower() for part in parts if str(part).strip()}


@dataclass(frozen=True)
class ForegroundInfo:
    hwnd: int = 0
    thread_id: int = 0
    process_id: int = 0
    process_name: str = ""
    keyboard_layout: int = 0

    def matches(self, targets: str | Iterable[str]) -> bool:
        expected = parse_target_processes(targets)
        return bool(expected and self.process_name.lower() in expected)


IS_WINDOWS = os.name == "nt"
_user32: Any = None
_kernel32: Any = None
_shell32: Any = None

if IS_WINDOWS:
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _shell32 = ctypes.windll.shell32

    _user32.GetForegroundWindow.argtypes = []
    _user32.GetForegroundWindow.restype = wintypes.HWND

    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    _user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
    _user32.GetKeyboardLayout.restype = ctypes.c_void_p

    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _user32.GetAsyncKeyState.restype = ctypes.c_short

    _user32.VkKeyScanExW.argtypes = [wintypes.WCHAR, ctypes.c_void_p]
    _user32.VkKeyScanExW.restype = ctypes.c_short

    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE

    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def get_foreground_info() -> ForegroundInfo:
    if not IS_WINDOWS:
        return ForegroundInfo()

    hwnd = int(_user32.GetForegroundWindow() or 0)
    if not hwnd:
        return ForegroundInfo()

    pid = wintypes.DWORD(0)
    thread_id = int(_user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or 0)

    process_name = ""
    if pid.value:
        process_handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if process_handle:
            try:
                capacity = 32768
                path_buffer = ctypes.create_unicode_buffer(capacity)
                size = wintypes.DWORD(capacity)
                if _kernel32.QueryFullProcessImageNameW(process_handle, 0, path_buffer, ctypes.byref(size)):
                    process_name = Path(path_buffer.value).name
            finally:
                _kernel32.CloseHandle(process_handle)

    keyboard_layout = int(_user32.GetKeyboardLayout(thread_id) or 0)
    return ForegroundInfo(
        hwnd=hwnd,
        thread_id=thread_id,
        process_id=int(pid.value),
        process_name=process_name,
        keyboard_layout=keyboard_layout,
    )


def is_running_as_admin() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return bool(_shell32.IsUserAnAdmin())
    except Exception:
        return False


VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12

SPECIAL_KEYS: dict[str, int] = {
    "BACKSPACE": 0x08,
    "BACK": 0x08,
    "TAB": 0x09,
    "ENTER": 0x0D,
    "RETURN": 0x0D,
    "SHIFT": VK_SHIFT,
    "CTRL": VK_CONTROL,
    "CONTROL": VK_CONTROL,
    "ALT": VK_MENU,
    "PAUSE": 0x13,
    "CAPSLOCK": 0x14,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "SPACE": 0x20,
    "PAGEUP": 0x21,
    "PGUP": 0x21,
    "PAGEDOWN": 0x22,
    "PGDN": 0x22,
    "END": 0x23,
    "HOME": 0x24,
    "LEFT": 0x25,
    "UP": 0x26,
    "RIGHT": 0x27,
    "DOWN": 0x28,
    "PRINTSCREEN": 0x2C,
    "PRTSC": 0x2C,
    "INSERT": 0x2D,
    "INS": 0x2D,
    "DELETE": 0x2E,
    "DEL": 0x2E,
    "0": 0x30,
    "1": 0x31,
    "2": 0x32,
    "3": 0x33,
    "4": 0x34,
    "5": 0x35,
    "6": 0x36,
    "7": 0x37,
    "8": 0x38,
    "9": 0x39,
    "A": 0x41,
    "B": 0x42,
    "C": 0x43,
    "D": 0x44,
    "E": 0x45,
    "F": 0x46,
    "G": 0x47,
    "H": 0x48,
    "I": 0x49,
    "J": 0x4A,
    "K": 0x4B,
    "L": 0x4C,
    "M": 0x4D,
    "N": 0x4E,
    "O": 0x4F,
    "P": 0x50,
    "Q": 0x51,
    "R": 0x52,
    "S": 0x53,
    "T": 0x54,
    "U": 0x55,
    "V": 0x56,
    "W": 0x57,
    "X": 0x58,
    "Y": 0x59,
    "Z": 0x5A,
    "LWIN": 0x5B,
    "RWIN": 0x5C,
    "NUMPAD0": 0x60,
    "NUMPAD1": 0x61,
    "NUMPAD2": 0x62,
    "NUMPAD3": 0x63,
    "NUMPAD4": 0x64,
    "NUMPAD5": 0x65,
    "NUMPAD6": 0x66,
    "NUMPAD7": 0x67,
    "NUMPAD8": 0x68,
    "NUMPAD9": 0x69,
    "MULTIPLY": 0x6A,
    "ADD": 0x6B,
    "SEPARATOR": 0x6C,
    "SUBTRACT": 0x6D,
    "DECIMAL": 0x6E,
    "DIVIDE": 0x6F,
    "NUMLOCK": 0x90,
    "SCROLLLOCK": 0x91,
}
for _index in range(1, 25):
    SPECIAL_KEYS[f"F{_index}"] = 0x6F + _index

RAW_KEY_ALIASES = {
    **{f"VK_{digit}": 0x30 + int(digit) for digit in "0123456789"},
    **{f"VK_{letter}": ord(letter) for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
}

CHAR_ALIASES = {
    "AMPERSAND": "&",
    "ET": "&",
    "PLUS_CHAR": "+",
    "MINUS_CHAR": "-",
    "ASTERISK_CHAR": "*",
}

MODIFIER_ALIASES = {
    "SHIFT": VK_SHIFT,
    "CTRL": VK_CONTROL,
    "CONTROL": VK_CONTROL,
    "ALT": VK_MENU,
}


@dataclass(frozen=True)
class KeyChord:
    vk_code: int
    modifiers: tuple[int, ...] = ()
    source: str = ""

    def display(self) -> str:
        names: list[str] = []
        for modifier in self.modifiers:
            if modifier == VK_CONTROL:
                names.append("CTRL")
            elif modifier == VK_SHIFT:
                names.append("SHIFT")
            elif modifier == VK_MENU:
                names.append("ALT")
            else:
                names.append(f"VK_{modifier:02X}")
        names.append(self.source or f"VK_{self.vk_code:02X}")
        return "+".join(names)


class KeySpecError(ValueError):
    pass


def _fallback_character_mapping(character: str) -> tuple[int, tuple[int, ...]]:
    # US-layout fallback used only for tests or non-Windows imports.
    if "a" <= character <= "z":
        return ord(character.upper()), ()
    if "A" <= character <= "Z":
        return ord(character), (VK_SHIFT,)
    if "0" <= character <= "9":
        return ord(character), ()
    symbols: dict[str, tuple[int, tuple[int, ...]]] = {
        "&": (ord("7"), (VK_SHIFT,)),
        "!": (ord("1"), (VK_SHIFT,)),
        "@": (ord("2"), (VK_SHIFT,)),
        "#": (ord("3"), (VK_SHIFT,)),
        "$": (ord("4"), (VK_SHIFT,)),
        "%": (ord("5"), (VK_SHIFT,)),
        "^": (ord("6"), (VK_SHIFT,)),
        "*": (ord("8"), (VK_SHIFT,)),
        "(": (ord("9"), (VK_SHIFT,)),
        ")": (ord("0"), (VK_SHIFT,)),
        " ": (0x20, ()),
    }
    if character in symbols:
        return symbols[character]
    raise KeySpecError(f"Caractère non pris en charge sur cette plateforme: {character!r}")


def _character_to_chord(character: str, keyboard_layout: int = 0) -> KeyChord:
    if len(character) != 1:
        raise KeySpecError("Une touche caractère doit contenir exactement un caractère.")

    if not IS_WINDOWS:
        vk_code, modifiers = _fallback_character_mapping(character)
        return KeyChord(vk_code=vk_code, modifiers=modifiers, source=character)

    layout = keyboard_layout or int(_user32.GetKeyboardLayout(0) or 0)
    mapped = int(_user32.VkKeyScanExW(character, ctypes.c_void_p(layout)))
    if mapped == -1:
        raise KeySpecError(f"Windows ne sait pas convertir le caractère {character!r} avec le clavier actif.")

    unsigned = mapped & 0xFFFF
    vk_code = unsigned & 0xFF
    shift_state = (unsigned >> 8) & 0xFF
    modifiers: list[int] = []
    if shift_state & 0x02:
        modifiers.append(VK_CONTROL)
    if shift_state & 0x01:
        modifiers.append(VK_SHIFT)
    if shift_state & 0x04:
        modifiers.append(VK_MENU)
    return KeyChord(vk_code=vk_code, modifiers=tuple(modifiers), source=character)


def _resolve_base_key(token: str, keyboard_layout: int, *, allow_character: bool = True) -> KeyChord:
    stripped = token.strip()
    upper = stripped.upper()
    if upper in RAW_KEY_ALIASES:
        return KeyChord(vk_code=RAW_KEY_ALIASES[upper], source=upper)
    if upper in SPECIAL_KEYS:
        return KeyChord(vk_code=SPECIAL_KEYS[upper], source=upper)
    if upper in CHAR_ALIASES:
        return _character_to_chord(CHAR_ALIASES[upper], keyboard_layout)
    if allow_character and len(stripped) == 1:
        # Letters and digits are intentionally treated as raw gaming keys.
        # Symbols such as '&' are translated through the active keyboard layout.
        if upper in SPECIAL_KEYS and (upper.isalpha() or upper.isdigit()):
            return KeyChord(vk_code=SPECIAL_KEYS[upper], source=upper)
        return _character_to_chord(stripped, keyboard_layout)
    raise KeySpecError(f"Touche inconnue: {token!r}")


def resolve_key_spec(spec: str, keyboard_layout: int = 0) -> KeyChord:
    cleaned = str(spec or "").strip()
    if not cleaned:
        raise KeySpecError("La touche ne peut pas être vide.")

    # Explicit combinations such as CTRL+F2 or SHIFT+1.
    if "+" in cleaned and cleaned != "+":
        tokens = [token.strip() for token in cleaned.split("+") if token.strip()]
        if len(tokens) < 2:
            raise KeySpecError(f"Combinaison de touches invalide: {spec!r}")
        modifier_codes: list[int] = []
        for token in tokens[:-1]:
            code = MODIFIER_ALIASES.get(token.upper())
            if code is None:
                raise KeySpecError(f"Modificateur inconnu: {token!r}")
            if code not in modifier_codes:
                modifier_codes.append(code)
        base = _resolve_base_key(tokens[-1], keyboard_layout)
        for implicit in base.modifiers:
            if implicit not in modifier_codes:
                modifier_codes.append(implicit)
        return KeyChord(vk_code=base.vk_code, modifiers=tuple(modifier_codes), source=tokens[-1].upper())

    return _resolve_base_key(cleaned, keyboard_layout)


def is_virtual_key_down(vk_code: int) -> bool:
    if not IS_WINDOWS:
        return False
    return bool(int(_user32.GetAsyncKeyState(int(vk_code))) & 0x8000)


def is_chord_down(chord: KeyChord, *, exact_modifiers: bool = False) -> bool:
    if not is_virtual_key_down(chord.vk_code):
        return False
    if not all(is_virtual_key_down(modifier) for modifier in chord.modifiers):
        return False
    if exact_modifiers:
        required = set(chord.modifiers)
        for modifier in (VK_CONTROL, VK_SHIFT, VK_MENU):
            if modifier == chord.vk_code:
                continue
            if modifier not in required and is_virtual_key_down(modifier):
                return False
    return True


def chord_signature(chord: KeyChord) -> tuple[int, tuple[int, ...]]:
    return chord.vk_code, tuple(sorted(set(chord.modifiers)))


def validate_port(value: Any, *, allow_zero: bool = False) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Port invalide: {value!r}") from exc
    minimum = 0 if allow_zero else 1
    if not minimum <= port <= 65535:
        raise ConfigError(f"Le port doit être compris entre {minimum} et 65535.")
    return port


def validate_pairing_key(value: Any) -> str:
    token = clean_text(value, max_length=128)
    if len(token) < 6:
        raise ConfigError("La clé d'appairage doit contenir au moins 6 caractères.")
    return token

from __future__ import annotations

import ctypes
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assist_common import APP_DIR, IS_WINDOWS, KeyChord, KeySpecError, resolve_key_spec


KEYEVENTF_KEYUP = 0x0002

DRIVER_CODES: dict[str, int] = {
    "anydriver": 0,
    "any": 0,
    "sendinput": 1,
    "logitech": 2,
    "razer": 3,
    "dd": 4,
    "mouclassinputinjection": 5,
    "mcii": 5,
    "logitechghubnew": 6,
    "ghubnew": 6,
}

DRIVER_DISPLAY_NAMES = (
    "Logitech",
    "LogitechGHubNew",
    "AnyDriver",
    "SendInput",
)


@dataclass(frozen=True)
class InputResult:
    ok: bool
    message: str
    chord: KeyChord | None = None


class LogitechInput:
    """Thin wrapper around the IbInputSimulator DLL used by the supplied project."""

    def __init__(self, dll_path: str = "IbInputSimulator.dll", driver: str = "Logitech") -> None:
        self._lock = threading.RLock()
        self._dll_path = dll_path
        self._driver = driver
        self._dll: Any = None
        self._fn_init: Any = None
        self._fn_destroy: Any = None
        self._fn_keybd_event: Any = None
        self._loaded_key: tuple[str, str] = ("", "")
        self._initialized = False
        self.last_error = ""

    @property
    def initialized(self) -> bool:
        with self._lock:
            return self._initialized

    @property
    def driver(self) -> str:
        return self._driver

    def configure(self, dll_path: str, driver: str) -> None:
        with self._lock:
            normalized = (str(dll_path).strip(), str(driver).strip())
            current = (self._dll_path, self._driver)
            if normalized != current:
                self._shutdown_unlocked()
                self._dll_path, self._driver = normalized

    def _absolute_dll_path(self) -> Path:
        path = Path(self._dll_path or "IbInputSimulator.dll")
        if not path.is_absolute():
            path = APP_DIR / path
        return path.resolve()

    def initialize(self) -> bool:
        with self._lock:
            if not IS_WINDOWS:
                self.last_error = "L'injection Logitech fonctionne uniquement sous Windows."
                return False
            if ctypes.sizeof(ctypes.c_void_p) != 8:
                self.last_error = "Python 64 bits est requis pour la DLL IbInputSimulator fournie."
                return False

            driver_name = (self._driver or "Logitech").strip()
            driver_code = DRIVER_CODES.get(driver_name.lower())
            if driver_code is None:
                self.last_error = f"Backend IbInputSimulator inconnu: {driver_name}"
                return False

            dll_path = self._absolute_dll_path()
            loaded_key = (str(dll_path), driver_name.lower())
            if self._initialized and self._loaded_key == loaded_key:
                return True

            self._shutdown_unlocked()
            if not dll_path.exists():
                self.last_error = f"DLL introuvable: {dll_path}"
                return False

            try:
                dll = ctypes.WinDLL(str(dll_path))
            except Exception as exc:
                self.last_error = f"Chargement de la DLL impossible: {exc}"
                return False

            try:
                fn_init = dll.IbSendInit
                fn_init.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
                fn_init.restype = ctypes.c_uint32

                fn_destroy = dll.IbSendDestroy
                fn_destroy.argtypes = []
                fn_destroy.restype = None

                fn_keybd_event = dll.IbSend_keybd_event
                fn_keybd_event.argtypes = [
                    ctypes.c_ubyte,
                    ctypes.c_ubyte,
                    ctypes.c_uint32,
                    ctypes.c_size_t,
                ]
                fn_keybd_event.restype = None
            except Exception as exc:
                self.last_error = f"Exports requis absents de la DLL: {exc}"
                return False

            try:
                error_code = int(fn_init(driver_code, 0, None))
            except Exception as exc:
                self.last_error = f"Échec de l'appel IbSendInit: {exc}"
                return False

            if error_code != 0:
                self.last_error = f"IbSendInit({driver_name}) a échoué avec le code {error_code}."
                return False

            self._dll = dll
            self._fn_init = fn_init
            self._fn_destroy = fn_destroy
            self._fn_keybd_event = fn_keybd_event
            self._loaded_key = loaded_key
            self._initialized = True
            self.last_error = ""
            return True

    def _send_event(self, vk_code: int, *, key_up: bool) -> None:
        if not self._fn_keybd_event:
            raise RuntimeError("IbInputSimulator n'est pas initialisé.")
        flags = KEYEVENTF_KEYUP if key_up else 0
        self._fn_keybd_event(ctypes.c_ubyte(vk_code), 0, flags, 0)

    def tap(self, key_spec: str, hold_ms: int = 45, keyboard_layout: int = 0) -> InputResult:
        try:
            chord = resolve_key_spec(key_spec, keyboard_layout)
        except KeySpecError as exc:
            return InputResult(False, str(exc))

        try:
            hold_ms = max(10, min(int(hold_ms), 2000))
        except (TypeError, ValueError):
            return InputResult(False, f"Durée d'appui invalide: {hold_ms!r}", chord)

        if not self.initialize():
            return InputResult(False, self.last_error, chord)

        pressed_modifiers: list[int] = []
        base_pressed = False
        with self._lock:
            try:
                for modifier in chord.modifiers:
                    self._send_event(modifier, key_up=False)
                    pressed_modifiers.append(modifier)
                    time.sleep(0.005)

                self._send_event(chord.vk_code, key_up=False)
                base_pressed = True
                time.sleep(hold_ms / 1000.0)
                self._send_event(chord.vk_code, key_up=True)
                base_pressed = False

                for modifier in reversed(pressed_modifiers):
                    time.sleep(0.003)
                    self._send_event(modifier, key_up=True)
                pressed_modifiers.clear()

                self.last_error = ""
                return InputResult(True, f"Touche {key_spec!r} envoyée via {self._driver}.", chord)
            except Exception as exc:
                self.last_error = f"IbSend_keybd_event a échoué: {exc}"
                return InputResult(False, self.last_error, chord)
            finally:
                # Avoid leaving a modifier or the base key logically pressed if an
                # exception occurs between key-down and key-up.
                if base_pressed:
                    try:
                        self._send_event(chord.vk_code, key_up=True)
                    except Exception:
                        pass
                for modifier in reversed(pressed_modifiers):
                    try:
                        self._send_event(modifier, key_up=True)
                    except Exception:
                        pass

    def _shutdown_unlocked(self) -> None:
        if self._initialized and self._fn_destroy:
            try:
                self._fn_destroy()
            except Exception:
                pass
        self._dll = None
        self._fn_init = None
        self._fn_destroy = None
        self._fn_keybd_event = None
        self._loaded_key = ("", "")
        self._initialized = False

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown_unlocked()

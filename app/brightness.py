"""Monitor gamma ramps only; never access the game or GPU settings.

The startup ramp is the baseline. Persist a recovery journal before every lift,
and leave recovery information intact if a write or an exact restore fails.
All ramp operations (including temporary previews) share one lock.
"""

import ctypes
import json
import os
import sys
import threading
import time
from ctypes import wintypes

from app import game_state, i18n, paths, settings

GAMMA_DIR = paths.USER_DIR / "gamma"
STATE_FILE = GAMMA_DIR / "state.json"
Ramp = (wintypes.WORD * 256) * 3
ATTACHED = 0x1
PRIMARY = 0x4
ENUM_CURRENT_SETTINGS = 0xFFFFFFFF


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


class DEVMODEW(ctypes.Structure):
    # The display member of DEVMODEW's 16-byte printer/display union.
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32), ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD), ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD), ("dmFields", wintypes.DWORD),
        ("dmPosition", wintypes.POINT), ("dmDisplayOrientation", wintypes.DWORD),
        ("dmDisplayFixedOutput", wintypes.DWORD), ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short), ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short),
        ("dmFormName", wintypes.WCHAR * 32), ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD), ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD), ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD), ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD), ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD), ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD), ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


_lock = threading.RLock()
_stop = threading.Event()
_wake = threading.Event()
_started = False
_user32 = _gdi32 = None
_originals = {}
_ready = set()
_journal = {}
_applied = {}
_dirty = set()
_temporary = None
_last_settings = None
_last_decision = None
_last_check = 0.0
_overridden = False
_error = None
_ERROR_KEYS = ("server.brightnessUnavailable", "server.brightnessMonitor",
               "server.brightnessFailed", "server.brightnessStorage",
               "server.brightnessRestoreFailed")


def _record_error(error):
    global _error
    _error = str(error) if str(error) in _ERROR_KEYS else "server.brightnessFailed"


def _windows():
    global _user32, _gdi32
    if _gdi32 is not None:
        return
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.EnumDisplayDevicesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                          ctypes.POINTER(DISPLAY_DEVICEW), wintypes.DWORD]
    user32.EnumDisplayDevicesW.restype = wintypes.BOOL
    user32.EnumDisplaySettingsW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                          ctypes.POINTER(DEVMODEW)]
    user32.EnumDisplaySettingsW.restype = wintypes.BOOL
    gdi32.CreateDCW.restype = wintypes.HDC
    gdi32.CreateDCW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
    for name in ("GetDeviceGammaRamp", "SetDeviceGammaRamp"):
        func = getattr(gdi32, name)
        func.argtypes = [wintypes.HDC, ctypes.c_void_p]
        func.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL
    _user32, _gdi32 = user32, gdi32


def monitors():
    if sys.platform != "win32":
        return []
    with _lock:
        try:
            _windows()
            out, index = [], 0
            while True:
                device = DISPLAY_DEVICEW()
                device.cb = ctypes.sizeof(device)
                if not _user32.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
                    break
                if device.StateFlags & ATTACHED:
                    mode = DEVMODEW()
                    mode.dmSize = ctypes.sizeof(mode)
                    if not _user32.EnumDisplaySettingsW(device.DeviceName, ENUM_CURRENT_SETTINGS, ctypes.byref(mode)):
                        raise RuntimeError("server.brightnessFailed")
                    out.append({"id": device.DeviceName, "number": len(out) + 1,
                                "primary": bool(device.StateFlags & PRIMARY),
                                "width": mode.dmPelsWidth, "height": mode.dmPelsHeight,
                                "x": mode.dmPosition.x, "y": mode.dmPosition.y})
                index += 1
            return out
        except Exception as error:
            _record_error(error)
            return []


def _ramp(device, data=None):
    """Prototype CreateDC/Get/Set code; caller holds _lock."""
    hdc = _gdi32.CreateDCW("DISPLAY", device, None, None)
    if not hdc:
        raise RuntimeError("server.brightnessFailed")
    try:
        ramp = Ramp()
        if data is None:
            if not _gdi32.GetDeviceGammaRamp(hdc, ctypes.byref(ramp)):
                raise RuntimeError("server.brightnessFailed")
            return [list(channel) for channel in ramp]
        for c in range(3):
            for i in range(256):
                ramp[c][i] = max(0, min(65535, int(data[c][i])))
        if not _gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(ramp)):
            raise RuntimeError("server.brightnessFailed")
    finally:
        _gdi32.DeleteDC(hdc)


def lifted(original, strength):
    """The prototype's dark-tone curve, sampled from the saved calibration."""
    s = strength / 100
    out = []
    for c in range(3):
        ch = []
        for i in range(256):
            x = i / 255
            y = min(1.0, x + 2.3 * s * x * (1 - x) ** 2)
            ch.append(original[c][round(y * 255)])
        out.append(ch)
    return out


def _valid_ramp(data):
    return (isinstance(data, list) and len(data) == 3
            and all(isinstance(ch, list) and len(ch) == 256
                    and all(type(value) is int and 0 <= value <= 65535 for value in ch)
                    for ch in data))


def _original_path(device):
    # Device IDs come from Windows, never from a request body or journal key.
    name = device.strip(chr(92)).replace('.', '').replace(chr(92), '_')
    return GAMMA_DIR / f"{name}.json"


def _save_json(path, data):
    try:
        GAMMA_DIR.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    except Exception as error:
        raise RuntimeError("server.brightnessStorage") from error


def _clear_journal(device):
    remaining = {key: value for key, value in _journal.items() if key != device}
    _save_json(STATE_FILE, remaining)
    _journal.pop(device, None)


def _restore_device(device):
    try:
        _ramp(device, _originals[device])
        if _ramp(device) != _originals[device]:
            raise RuntimeError("server.brightnessRestoreFailed")
        _applied.pop(device, None)
        if device in _journal:
            _clear_journal(device)
        _dirty.discard(device)
        return True
    except Exception as error:
        _record_error(error if str(error) == "server.brightnessStorage"
                      else RuntimeError("server.brightnessRestoreFailed"))
        return False


def _restore_devices(devices):
    # Try every device even when one is unplugged or its driver rejects a write.
    results = [_restore_device(device) for device in list(devices)]
    return all(results)


def init():
    """Capture startup baselines once, recovering only an exact journal match."""
    global _started, _journal
    if sys.platform != "win32":
        return
    with _lock:
        if _started or _stop.is_set():
            return
        _started = True
        try:
            attached = monitors()
            if not attached:
                raise RuntimeError("server.brightnessUnavailable")
            try:
                _journal = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except FileNotFoundError:
                _journal = {}
            # Fail closed if recovery metadata is corrupt: don't replace baselines.
            if not isinstance(_journal, dict) or any(
                not isinstance(record, dict) or type(record.get("applied")) is not bool
                or not _valid_ramp(record.get("ramp")) for record in _journal.values()
            ):
                raise RuntimeError("server.brightnessStorage")
            for monitor in attached:
                device = monitor["id"]
                try:
                    current = _ramp(device)
                    record = _journal.get(device, {})
                    if record.get("applied") and current == record["ramp"]:
                        original = json.loads(_original_path(device).read_text(encoding="utf-8"))
                        if not _valid_ramp(original):
                            raise RuntimeError("server.brightnessStorage")
                        _originals[device] = original
                        _dirty.add(device)
                        if not _restore_device(device):
                            continue
                        # Keep the old original file after crash recovery.
                    else:
                        _save_json(_original_path(device), current)
                        _originals[device] = current
                        if device in _journal:
                            _clear_journal(device)
                    _ready.add(device)
                except (OSError, ValueError):
                    _record_error(RuntimeError("server.brightnessStorage"))
                except Exception as error:
                    _record_error(error)
            if _ready:
                threading.Thread(target=_run, name="brightness", daemon=True).start()
        except (OSError, ValueError):
            _record_error(RuntimeError("server.brightnessStorage"))
        except Exception as error:
            _record_error(error)


def _selected(config):
    attached = monitors()
    selected = config["brightness_monitor"]
    if selected:
        return selected if any(m["id"] == selected for m in attached) else None
    primary = next((m for m in attached if m["primary"]), None)
    return (primary or (attached[0] if attached else {})).get("id")


def _apply(device, strength):
    if device not in _ready:
        raise RuntimeError("server.brightnessMonitor")
    ramp = lifted(_originals[device], strength)
    record = {"applied": True, "ramp": ramp}
    _save_json(STATE_FILE, {**_journal, device: record})
    _journal[device] = record
    _dirty.add(device)  # A failed GDI call may still have changed the display.
    try:
        _ramp(device, ramp)
    except Exception:
        if not _restore_device(device):
            raise RuntimeError("server.brightnessRestoreFailed")
        raise
    _applied[device] = ramp


def _tick():
    global _last_settings, _last_decision, _temporary, _last_check, _overridden, _error
    if not _ready or _stop.is_set():
        return
    config = settings.load()
    signature = tuple(config[key] for key in ("brightness_mode", "brightness_strength", "brightness_monitor"))
    now = time.monotonic()
    if signature != _last_settings or (_temporary and now >= _temporary[2]):
        _temporary = None
    _last_settings = signature
    mode, strength, _ = signature
    want_on = strength > 0 and mode == "always"
    if strength > 0 and mode == "raid" and not _temporary:
        try:
            want_on = game_state.get()["status"] == "raid"
        except Exception as error:
            _record_error(error)
    decision = (signature, want_on, _temporary)
    if decision != _last_decision:
        # Record the attempt even if it fails: never fight a driver every second.
        if _last_decision is not None:
            _error = None
        _last_decision = decision
        _overridden = False
        if not _restore_devices(_dirty):
            return
        if _temporary:
            device, strength, _ = _temporary
        else:
            device = _selected(config) if want_on else None
        if (_temporary or want_on) and strength > 0:
            _apply(device, strength)
        _last_check = now
    if _applied and now - _last_check >= 3:
        _last_check = now
        for device, ramp in _applied.items():
            if _ramp(device) != ramp:
                _overridden = True


def _run():
    while not _stop.is_set():
        _wake.clear()
        with _lock:
            try:
                _tick()
            except Exception as error:
                _record_error(error)
            delay = min(1, max(0, _temporary[2] - time.monotonic())) if _temporary else 1
        _wake.wait(delay)


def status():
    with _lock:
        config = settings.load()
        attached = monitors()
        return {"available": sys.platform == "win32" and not _stop.is_set()
                             and any(m["id"] in _ready for m in attached),
                "monitors": attached, "mode": config["brightness_mode"],
                "strength": config["brightness_strength"], "monitor": config["brightness_monitor"],
                "applied": bool(_applied) and not _overridden, "overridden": _overridden,
                "error": i18n.t(_error) if _error else None}


def configure(changes):
    with _lock:
        if sys.platform == "win32" and not _stop.is_set():
            try:
                settings.save({f"brightness_{key}": value for key, value in changes.items()
                               if key in ("mode", "strength", "monitor")})
                _tick()
            except OSError:
                _record_error(RuntimeError("server.brightnessStorage"))
            except Exception as error:
                _record_error(error)
        return status()


def _start_temporary(device, strength, seconds):
    global _temporary
    if device not in _ready:
        raise RuntimeError("server.brightnessMonitor")
    _temporary = (device, strength, time.monotonic() + seconds)
    _tick()
    _wake.set()  # Honor the 1 s identification deadline even between worker ticks.


def preview(seconds=5):
    with _lock:
        if sys.platform == "win32" and not _stop.is_set():
            try:
                _tick()
                config = settings.load()
                _start_temporary(_selected(config), config["brightness_strength"], seconds)
            except Exception as error:
                _record_error(error)
        return status()


def identify(device):
    with _lock:
        if sys.platform == "win32" and not _stop.is_set():
            try:
                _tick()
                selected = _selected({"brightness_monitor": device}) if isinstance(device, str) else None
                _start_temporary(selected, 60, 1)
            except Exception as error:
                _record_error(error)
        return status()


def restore():
    """Explicit restore also disables the mode and cancels temporary effects."""
    global _temporary, _last_decision, _overridden, _error
    with _lock:
        if sys.platform == "win32":
            _temporary = None
            _last_decision = None
            _overridden = False
            _error = None
            try:
                settings.save({"brightness_mode": "off"})
            except Exception:
                # Stop the worker if off cannot be saved; never reapply after restore.
                _stop.set()
                _wake.set()
                _record_error(RuntimeError("server.brightnessStorage"))
            _restore_devices(_originals)
        return status()


def shutdown():
    """Stop future writes before restoring; safe for both window and atexit."""
    if sys.platform != "win32":
        return
    with _lock:
        _stop.set()
        _wake.set()
        _restore_devices(_originals)

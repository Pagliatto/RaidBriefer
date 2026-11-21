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
QDC_ONLY_ACTIVE_PATHS = 0x2
DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME = 1
DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME = 2
ERROR_INSUFFICIENT_BUFFER = 122


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


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class DISPLAYCONFIG_RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", wintypes.UINT), ("Denominator", wintypes.UINT)]


class DISPLAYCONFIG_PATH_SOURCE_INFO(ctypes.Structure):
    _fields_ = [("adapterId", LUID), ("id", wintypes.UINT),
                ("modeInfoIdx", wintypes.UINT), ("statusFlags", wintypes.UINT)]


class DISPLAYCONFIG_PATH_TARGET_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", LUID), ("id", wintypes.UINT), ("modeInfoIdx", wintypes.UINT),
        ("outputTechnology", wintypes.UINT), ("rotation", wintypes.UINT),
        ("scaling", wintypes.UINT), ("refreshRate", DISPLAYCONFIG_RATIONAL),
        ("scanLineOrdering", wintypes.UINT), ("targetAvailable", wintypes.BOOL),
        ("statusFlags", wintypes.UINT),
    ]


class DISPLAYCONFIG_PATH_INFO(ctypes.Structure):
    _fields_ = [("sourceInfo", DISPLAYCONFIG_PATH_SOURCE_INFO),
                ("targetInfo", DISPLAYCONFIG_PATH_TARGET_INFO), ("flags", wintypes.UINT)]


class DISPLAYCONFIG_DEVICE_INFO_HEADER(ctypes.Structure):
    _fields_ = [("type", wintypes.UINT), ("size", wintypes.UINT),
                ("adapterId", LUID), ("id", wintypes.UINT)]


class DISPLAYCONFIG_SOURCE_DEVICE_NAME(ctypes.Structure):
    _fields_ = [("header", DISPLAYCONFIG_DEVICE_INFO_HEADER),
                ("viewGdiDeviceName", wintypes.WCHAR * 32)]


class DISPLAYCONFIG_TARGET_DEVICE_NAME(ctypes.Structure):
    _fields_ = [
        ("header", DISPLAYCONFIG_DEVICE_INFO_HEADER), ("flags", wintypes.UINT),
        ("outputTechnology", wintypes.UINT), ("edidManufactureId", wintypes.WORD),
        ("edidProductCodeId", wintypes.WORD), ("connectorInstance", wintypes.UINT),
        ("monitorFriendlyDeviceName", wintypes.WCHAR * 64),
        ("monitorDevicePath", wintypes.WCHAR * 128),
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
               "server.brightnessRestoreFailed", "server.brightnessOtherApp")
MUTEX_NAME = r"Local\RaidBriefer.Brightness"  # 밝기를 잡은 프로그램 표시 (프로그램이 꺼지면 Windows 가 자동으로 풂)
ERROR_ALREADY_EXISTS = 183
_mutex = None


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


def _monitor_names():
    """Windows 디스플레이 설정의 실제 이름을 GDI 장치 이름에 연결한다. 실패해도 밝기는 사용 가능."""
    names = {}
    try:
        get_sizes = _user32.GetDisplayConfigBufferSizes
        get_sizes.argtypes = [wintypes.UINT, ctypes.POINTER(wintypes.UINT),
                              ctypes.POINTER(wintypes.UINT)]
        get_sizes.restype = wintypes.LONG
        query = _user32.QueryDisplayConfig
        query.argtypes = [wintypes.UINT, ctypes.POINTER(wintypes.UINT),
                          ctypes.POINTER(DISPLAYCONFIG_PATH_INFO),
                          ctypes.POINTER(wintypes.UINT), ctypes.c_void_p, ctypes.c_void_p]
        query.restype = wintypes.LONG
        get_info = _user32.DisplayConfigGetDeviceInfo
        get_info.argtypes = [ctypes.POINTER(DISPLAYCONFIG_DEVICE_INFO_HEADER)]
        get_info.restype = wintypes.LONG
        for _ in range(3):  # 크기 조회 중 모니터가 연결/해제되면 버퍼 크기부터 다시 확인한다.
            path_count, mode_count = wintypes.UINT(), wintypes.UINT()
            if get_sizes(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(path_count), ctypes.byref(mode_count)) != 0:
                return names
            display_paths = (DISPLAYCONFIG_PATH_INFO * path_count.value)()
            # DISPLAYCONFIG_MODE_INFO는 64바이트, 8바이트 정렬. 이름 조회에는 내용이 필요 없다.
            modes = (ctypes.c_uint64 * (8 * mode_count.value))()
            result = query(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(path_count), display_paths,
                           ctypes.byref(mode_count), modes, None)
            if result == ERROR_INSUFFICIENT_BUFFER:
                continue
            if result != 0:
                return names
            for path in display_paths[:path_count.value]:
                source = DISPLAYCONFIG_SOURCE_DEVICE_NAME()
                source.header = DISPLAYCONFIG_DEVICE_INFO_HEADER(
                    DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME, ctypes.sizeof(source),
                    path.sourceInfo.adapterId, path.sourceInfo.id)
                target = DISPLAYCONFIG_TARGET_DEVICE_NAME()
                target.header = DISPLAYCONFIG_DEVICE_INFO_HEADER(
                    DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME, ctypes.sizeof(target),
                    path.targetInfo.adapterId, path.targetInfo.id)
                if (get_info(ctypes.byref(source.header)) == 0
                        and get_info(ctypes.byref(target.header)) == 0):
                    name = target.monitorFriendlyDeviceName.strip()
                    if name:
                        names.setdefault(source.viewGdiDeviceName, name)
            return names
    except Exception:
        pass  # 원격 세션·지원하지 않는 드라이버 등에서는 번호로 표시한다.
    return names


def monitors():
    if sys.platform != "win32":
        return []
    with _lock:
        try:
            _windows()
            names = _monitor_names()
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
                                "name": names.get(device.DeviceName, ""),
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
    global _started, _journal, _mutex
    if sys.platform != "win32":
        return
    with _lock:
        if _started or _stop.is_set():
            return
        _started = True
        try:
            # 밝기는 먼저 켜진 RaidBriefer 하나만 조절한다. 두 개가 같은 모니터를 잡으면
            # 서로의 '원래 밝기'를 덮어써서 레이드 중에 밝기가 혼자 바뀐다.
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
            _mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
            if not _mutex or ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
                raise RuntimeError("server.brightnessOtherApp")
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


_last_config = None


def _tick():
    global _last_settings, _last_decision, _temporary, _last_check, _overridden, _error, _last_config
    if not _ready or _stop.is_set():
        return
    now = time.monotonic()
    if _temporary and now >= _temporary[2]:
        # 설정 파일이 잠겨도 임시 효과의 원상 복원은 먼저 실행한다.
        _temporary = None
        _last_decision = None
        if not _restore_devices(_dirty):
            return False
    read_ok = True
    try:
        config = settings.load()
        _last_config = dict(config)
    except (settings.DataReadError, OSError):
        read_ok = False
        config = _last_config or settings.DEFAULTS
        _record_error(RuntimeError("server.brightnessStorage"))
    signature = tuple(config[key] for key in ("brightness_mode", "brightness_strength", "brightness_monitor"))
    if signature != _last_settings:
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
    return read_ok


def _run():
    while not _stop.is_set():
        _wake.clear()
        with _lock:
            tick_ok = False
            try:
                tick_ok = _tick()
            except Exception as error:
                _record_error(error)
            # 읽기/복원 실패에는 지난 임시 만료 시각 때문에 무대기 반복하지 않는다.
            delay = min(1, max(0.01, _temporary[2] - time.monotonic())) if tick_ok and _temporary else 1
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

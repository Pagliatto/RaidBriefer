"""Read-only story places; malformed or missing data is harmless."""
import json
import math
import threading

from app import paths

FILE = paths.DATA_DIR / "story" / "places.json"
_lock = threading.Lock()
_cached = None


def finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def valid(p) -> bool:
    """화면에 쓸 수 있는 장소인지 (편집 도구도 같은 기준으로 파일 순번을 맞춤)"""
    return (isinstance(p, dict) and isinstance(p.get("map"), str) and bool(p["map"])
            and all(finite_number(p.get(k)) for k in ("x", "y", "z"))
            and isinstance(p.get("candidate", False), bool) and isinstance(p.get("note", ""), str))


def load() -> dict:
    global _cached
    with _lock:
        try:
            stamp = FILE.stat().st_mtime_ns
            if _cached is not None and _cached[0] == stamp:
                return _cached[1]
            data = json.loads(FILE.read_text(encoding="utf-8"))
            places = data.get("places", {}) if isinstance(data, dict) else {}
            result = {}
            if isinstance(places, dict):
                for sid, entries in places.items():
                    if not isinstance(entries, list):
                        continue
                    good = [p for p in entries if valid(p)]
                    if good:
                        result[sid] = good
            _cached = (stamp, result)
            return result
        except (OSError, ValueError, RecursionError):
            _cached = None
            return {}

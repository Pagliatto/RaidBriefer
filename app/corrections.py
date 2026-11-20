"""게임 사실 보완 파일을 한 곳에서 읽는다 (없거나 깨지면 빈 값)."""

import json
import threading

from app import paths

FILE = paths.DATA_DIR / "corrections.json"
_lock = threading.Lock()
_cached: tuple[int, dict] | None = None


def load() -> dict:
    global _cached
    with _lock:
        try:
            mtime = FILE.stat().st_mtime_ns
            if _cached is not None and _cached[0] == mtime:
                return _cached[1]
            data = json.loads(FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
            _cached = (mtime, data)
            return data
        except (OSError, ValueError):
            _cached = None
            return {}


def section(name: str) -> dict:
    """설명 키와 잘못된 항목은 앱에서 사용하지 않는다."""
    data = load().get(name)
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items()
            if not key.startswith("_") and isinstance(value, dict)}

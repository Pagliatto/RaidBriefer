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


def manual_extracts(map_key: str, dev_extracts: list) -> list[dict]:
    """tarkov.dev 에 없는 탈출구 (위키 표 기준으로 직접 적은 것). dev 모양 {name, faction, position} 으로.
    dev 에 같은 이름(게임 id 또는 영어 이름)이 생기면 dev 가 이김 → 빼고 돌려준다."""
    entry = section("extracts").get(map_key) or {}
    dev_names = {(e.get("name") or "").strip() for e in dev_extracts or []}
    result = []
    for e in entry.get("extracts") or []:
        if not isinstance(e, dict) or not e.get("name"):
            continue
        if e["name"] in dev_names or e.get("name_en") in dev_names:
            continue
        try:
            position = {"x": float(e["x"]), "y": float(e.get("y", 0)), "z": float(e["z"])}
        except (KeyError, TypeError, ValueError):
            continue
        result.append({"name": e["name"], "name_en": e.get("name_en") or e["name"],
                       "faction": e.get("faction", "shared"), "position": position,
                       "source": entry.get("source", "manual")})
    return result


def section(name: str) -> dict:
    """설명 키와 잘못된 항목은 앱에서 사용하지 않는다."""
    data = load().get(name)
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items()
            if not key.startswith("_") and isinstance(value, dict)}

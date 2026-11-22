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


_EMPTY = {"noPlace": [], "extractLinks": {}, "stepMaps": {}}
_extras_cache = [None, _EMPTY]


def _extras(data) -> dict:
    """장소가 필요 없는 단계 목록과, 이름 있는 탈출구로 대신하는 단계 (잘못된 항목은 뺌)"""
    data = data if isinstance(data, dict) else {}
    no_place = [s for s in data.get("no_place", []) if isinstance(s, str) and s] if isinstance(data.get("no_place"), list) else []
    links = {}
    raw = data.get("extract_links")
    if isinstance(raw, dict):
        for sid, link in raw.items():
            if (isinstance(sid, str) and isinstance(link, dict) and isinstance(link.get("map"), str) and link["map"]
                    and isinstance(link.get("extract"), str) and link["extract"]):
                links[sid] = {"map": link["map"], "extract": link["extract"]}
    # 편집자가 고친 단계별 맵 목록 (스토리 데이터의 map 대신 씀)
    step_maps = {}
    raw = data.get("step_maps")
    if isinstance(raw, dict):
        for sid, maps in raw.items():
            if isinstance(sid, str) and isinstance(maps, list) and maps and all(isinstance(m, str) and m for m in maps):
                step_maps[sid] = list(dict.fromkeys(maps))
    return {"noPlace": no_place, "extractLinks": links, "stepMaps": step_maps}


def extras() -> dict:
    load()  # 파일이 바뀌었으면 같이 다시 읽음
    return _extras_cache[1] if _extras_cache[0] is not None else _EMPTY


def step_maps() -> dict:
    return extras().get("stepMaps", {})


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
            _extras_cache[:] = [stamp, _extras(data)]
            return result
        except (OSError, ValueError, RecursionError):
            _cached = None
            return {}

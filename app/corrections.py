"""게임 사실 보완 파일을 한 곳에서 읽는다 (없거나 깨지면 빈 값)."""

import json
import math
import re
from datetime import date
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


def manual_boss_zones(map_key: str) -> list[dict]:
    """출처·위치 이름을 보존한 보스 구역. 잘못된 항목만 건너뛴다."""
    entry = section("boss_zones").get(map_key, {})
    zones = entry.get("zones")
    result = []
    for zone in zones if isinstance(zones, list) else []:
        if not isinstance(zone, dict):
            continue
        if not all(type(zone.get(k)) in (int, float) and math.isfinite(zone[k])
                   for k in ("x", "y", "z", "radius")) or zone["radius"] <= 0:
            continue
        name = zone.get("spawn_location")
        bosses = zone.get("bosses")
        if not isinstance(name, str) or not name or not isinstance(bosses, list):
            continue
        bosses = list(dict.fromkeys(b for b in bosses if isinstance(b, str) and b))
        if bosses:
            result.append({**zone, "bosses": bosses, "source": entry.get("source", "manual")})
    return result


def manual_enemy_spawns(map_key: str) -> dict:
    """BD 위치 이름별 보정. 출처·좌표·인원 형식이 깨진 항목만 제외한다."""
    entries = section("enemy_spawns").get(map_key, {})
    result = {}
    for name, entry in entries.items():
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) or not isinstance(entry, dict):
            continue
        if entry.get("from") not in ("game", "wiki"):
            continue
        try:
            checked = entry.get("checked")
            if not isinstance(checked, str) or date.fromisoformat(checked).isoformat() != checked:
                continue
        except ValueError:
            continue
        if not all(type(entry.get(k)) in (int, float) and math.isfinite(entry[k]) for k in ("x", "y", "z")):
            continue
        counts = entry.get("count")
        if not isinstance(counts, list) or len(counts) != 2 or not all(type(n) is int and n >= 1 for n in counts) or counts[0] > counts[1]:
            continue
        if not isinstance(entry.get("note"), str) or not entry["note"].strip():
            continue
        target = entry.get("merge_into")
        if target is not None and (not isinstance(target, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", target) or target == name):
            continue
        if any(flag in entry and type(entry[flag]) is not bool for flag in ("inside", "estimated")):
            continue
        result[name] = {**entry, "count": list(counts)}
    # 다단계·순환 병합은 허용하지 않는다. 잘못된 보정 때문에 dev 구역을 잃지 않게 한다.
    return {name: entry for name, entry in result.items()
            if not entry.get("merge_into") or (entry["merge_into"] in result
                                               and not result[entry["merge_into"]].get("merge_into"))}

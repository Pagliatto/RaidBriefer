"""읽기 전용 지도 공략. 파일이 없거나 깨지면 읽을 수 있는 항목만 돌려준다."""

from copy import deepcopy
import json
import math
import re
import threading

from app import corrections, paths, settings, tarkov_api
from app.map_views import _xzy

GUIDES_DIR = paths.DATA_DIR / "guides"
_MAP_KEY = re.compile(r"[a-z0-9][a-z0-9_-]{0,59}")
_KINDS = {"key_spawn", "panel", "toolset", "explosive", "door", "building", "extract", "start"}
_lock = threading.Lock()
_cached: dict = {}


def _empty(map_key: str) -> dict:
    return {"map": map_key, "prep": [], "steps": [], "warnings": [],
            "points": {}, "sources": {}, "checked": ""}


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _position(value) -> list | None:
    if not isinstance(value, dict):
        return None
    # bool, 문자열, 누락된 높이, NaN/Infinity는 좌표로 쓰지 않는다.
    if not all(type(value.get(axis)) is int or
               (type(value.get(axis)) is float and math.isfinite(value[axis]))
               for axis in ("x", "y", "z")):
        return None
    return _xzy(value)


def _texts(rows, sources: dict) -> list[dict]:
    result = []
    for row in _list(rows):
        if not isinstance(row, dict):
            continue
        ko, en = _text(row.get("ko")), _text(row.get("en"))
        if not ko and not en:
            continue
        source = _text(row.get("source"))
        certainty = _text(row.get("certainty"))
        result.append({"ko": ko, "en": en,
                       "source": source if source in sources else None,
                       "certainty": certainty if certainty in ("wiki", "user", "unconfirmed") else "unconfirmed"})
    return result


def _steps(rows, points: dict, sources: dict) -> list[dict]:
    result = []
    seen = set()
    for row in _list(rows):
        if not isinstance(row, dict):
            continue
        row_id = _text(row.get("id"))
        texts = _texts([row], sources)
        if not row_id or row_id in seen or not texts:
            continue
        seen.add(row_id)
        result.append({"id": row_id, "story_step": _text(row.get("story_step")) or None,
                       **texts[0],
                       "points": [key for key in _list(row.get("points"))
                                  if isinstance(key, str) and key in points],
                       "warnings": _texts(row.get("warnings"), sources)})
    return result


def _prepare(data, map_key: str) -> dict:
    result = _empty(map_key)
    if not isinstance(data, dict) or data.get("map") != map_key:
        return result
    result["checked"] = _text(data.get("checked"))
    sources = data.get("sources")
    if isinstance(sources, dict):
        result["sources"] = {
            key: {field: _text(value.get(field)) for field in ("from", "link", "section")}
            for key, value in sources.items() if isinstance(value, dict)
        }
    points = data.get("points")
    if isinstance(points, dict):
        for key, point in points.items():
            if not key or not isinstance(point, dict):
                continue
            pos = _position(point)
            kind = _text(point.get("kind"))
            ko, en = _text(point.get("label_ko")), _text(point.get("label_en"))
            if pos is None or kind not in _KINDS or not (ko or en):
                continue
            result["points"][key] = {"pos": pos, "kind": kind, "label_ko": ko, "label_en": en,
                                     "candidate": point.get("candidate") is True}
    for section in ("prep", "steps"):
        result[section] = _steps(data.get(section), result["points"], result["sources"])
    result["warnings"] = _texts(data.get("warnings"), result["sources"])
    return result


def _read(map_key: str) -> dict:
    path = GUIDES_DIR / f"{map_key}.json"
    with _lock:
        try:
            mtime = path.stat().st_mtime_ns
            hit = _cached.get(path)
            if hit is not None and hit[0] == mtime:
                return deepcopy(hit[1])
            try:
                data = _prepare(json.loads(path.read_text(encoding="utf-8")), map_key)
            except (ValueError, RecursionError):
                data = _empty(map_key)
            # 없는 맵은 저장하지 않고, 실제 파일 캐시도 장시간 사용 시 32개로 제한한다.
            if path not in _cached and len(_cached) >= 32:
                del _cached[next(iter(_cached))]
            _cached[path] = (mtime, data)
            return deepcopy(data)
        except OSError:
            _cached.pop(path, None)
            return _empty(map_key)


def _local_data(name: str) -> dict:
    # load는 저장본만 읽는다. 갱신·다운로드나 언어별 Translator는 호출하지 않는다.
    try:
        data = tarkov_api.load(name)
    except (tarkov_api.DataError, settings.DataReadError, OSError, ValueError, RecursionError):
        # 저장본 오류 문구를 만들 때 언어 설정 읽기도 실패할 수 있다. 공략은 계속 제공한다.
        return {}
    return data if isinstance(data, dict) else {}


def _names(entry: dict) -> set[str]:
    return {value.strip() for key in ("name", "name_en")
            if (value := _text(entry.get(key))).strip()}


def _dev_positions(guide: dict) -> None:
    points = [point for point in guide["points"].values() if point["kind"] == "extract"]
    if not points:
        return
    maps = _local_data("maps").get("maps")
    if not isinstance(maps, dict):
        return
    # map_views.features와 같이 변형 맵의 탈출구를 섞지 않고 기본 맵만 쓴다.
    base = next((m for m in maps.values() if isinstance(m, dict)
                 and m.get("normalizedName") == guide["map"]), {})
    extracts = _list(base.get("extracts"))
    if not extracts:
        return
    english = _local_data("maps_en")
    positions = {}
    for entry in extracts:
        if not isinstance(entry, dict):
            continue
        pos = _position(entry.get("position"))
        if pos is None:
            continue
        names = _names(entry)
        names.update(_text(english.get(name)).strip() for name in tuple(names))
        for name in names - {""}:
            positions.setdefault(name, pos)
    try:
        manual = corrections.section("extracts").get(guide["map"], {})
    except (OSError, ValueError, RecursionError):
        return
    for point in points:
        # 현재 자료에는 별도 extract id가 없다. 이름 뒤 설명 구분자만 떼어 정확히 비교한다.
        # 예: "Zubr Boat: boat location" / "Zubr Boat · ...". 부분·유사 이름 검색은 하지 않는다.
        labels = {re.split(r"\s*[·:]\s*", point[key], maxsplit=1)[0].strip()
                  for key in ("label_ko", "label_en")}
        matches = [entry for entry in _list(manual.get("extracts"))
                   if isinstance(entry, dict) and labels & _names(entry)]
        if len(matches) != 1:
            continue
        entry = matches[0]
        for name in (_text(entry.get("name")).strip(), _text(entry.get("name_en")).strip()):
            if name in positions:
                point["pos"] = positions[name].copy()
                break


def load(map_key: str) -> dict:
    """기본 맵 공략. ko/en 원문과 참조 id는 보존하고 좌표만 화면 형식으로 바꾼다."""
    from app.quests import MERGED_MAPS

    map_key = MERGED_MAPS.get(map_key, map_key)
    if not _MAP_KEY.fullmatch(map_key):
        return _empty(map_key)  # 경로 구분자·드라이브·상위 폴더 접근 차단
    guide = _read(map_key)
    # guide 캐시와 별개로 최신 로컬 dev 저장본을 확인한다. 원본 캐시는 바꾸지 않는다.
    _dev_positions(guide)
    return guide

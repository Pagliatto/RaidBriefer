"""지도 그림과 좌표 맞추는 값 (지도 화면용).

- 좌표 값: tarkov.dev 웹사이트 코드의 src/data/maps.json (MIT 라이선스)
  맵마다 SVG 그림 주소, transform(배율·이동), coordinateRotation(회전), bounds(범위), 그린 사람
- 지도 그림: the-hideout/tarkov-dev-svg-maps (CC BY-NC-SA 4.0 → 비상업 + 출처 표시)
- 둘 다 cache/ 폴더에 저장해 두고, 좌표 값은 하루에 한 번만 새로 확인한다.
  (인터넷이 안 되거나 받기에 실패하면 저장본을 그대로 씀)
"""

import json
import math
import re
import threading
import time
import uuid
import urllib.request
from pathlib import Path

from app import corrections, i18n, paths, tarkov_api

VIEWS_URL = "https://raw.githubusercontent.com/the-hideout/tarkov-dev/main/src/data/maps.json"
SVG_HOST = "https://assets.tarkov.dev/maps/svg/"
CACHE_DIR = paths.CACHE_DIR
VIEWS_FILE = CACHE_DIR / "map_views.json"
SVG_DIR = CACHE_DIR / "map_svg"
CHECK_SECONDS = 24 * 3600
SAFE_SVG_NAME = re.compile(r"^[A-Za-z0-9_\-]+\.svg$")

_lock = threading.Lock()
_views_read_lock = threading.Lock()
_views_loaded = None
_views_retry_after = 0.0
_views_error = None


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "RaidBriefer"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read()


def _validate_views(data) -> None:
    if not isinstance(data, list) or not all(
        isinstance(group, dict) and isinstance(group.get("maps", []), list)
        and all(isinstance(view, dict) for view in group.get("maps", []))
        for group in data
    ):
        raise ValueError(i18n.t("data.notSaved", name="map_views"))


def _read_views() -> tuple:
    global _views_loaded
    # 이 잠금은 파일 읽기/parse만 보호한다. 네트워크 일꾼은 잡지 않는다.
    started = time.perf_counter()
    with _views_read_lock:
        tarkov_api.profile_wait("map_views_read", started)
        try:
            stat = VIEWS_FILE.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            if _views_loaded and _views_loaded[0] == signature:
                return _views_loaded
            data = json.loads(VIEWS_FILE.read_text(encoding="utf-8"))
            _validate_views(data)
            _views_loaded = (signature, data, stat.st_mtime)
            return _views_loaded
        except PermissionError:
            if _views_loaded:
                return _views_loaded
            raise


def _refresh_views() -> None:
    global _views_retry_after, _views_error
    temp = None
    try:
        data = _download(VIEWS_URL)
        _validate_views(json.loads(data))
        CACHE_DIR.mkdir(exist_ok=True)
        temp = VIEWS_FILE.with_name(f"{VIEWS_FILE.name}.{uuid.uuid4().hex}.tmp")
        temp.write_bytes(data)
        temp.replace(VIEWS_FILE)  # 읽는 요청은 완전한 이전 파일 또는 새 파일만 본다.
        _views_error = None
        _views_retry_after = 0.0
    except Exception as error:
        _views_error = str(error)
        # 저장본의 mtime을 바꾸지 않는다. 시계 변경과 쓰기 제한에서도 재시도 폭주 방지.
        _views_retry_after = time.monotonic() + (CHECK_SECONDS if _views_loaded else 60)
    finally:
        if temp is not None:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        _lock.release()


def _load_views() -> list:
    """저장본은 즉시 제공하고, 하루 지난 설정은 하나의 일꾼이 갱신한다."""
    try:
        saved = _read_views()
    except (ValueError, OSError):
        saved = None
    fresh = saved is not None and 0 <= time.time() - saved[2] < CHECK_SECONDS
    if not fresh and time.monotonic() >= _views_retry_after and _lock.acquire(blocking=False):
        try:
            threading.Thread(target=_refresh_views, name="map-views", daemon=True).start()
        except Exception:
            _lock.release()
            if saved is None:
                raise
    if saved is not None:
        return saved[1]
    started = time.perf_counter()
    with _lock:
        tarkov_api.profile_wait("map_views_refresh", started)
    try:
        return _read_views()[1]
    except (ValueError, OSError) as error:
        raise RuntimeError(_views_error or str(error)) from error


def view_for(normalized_name: str) -> dict | None:
    """맵 구분값(예: customs) → 화면이 쓸 지도 설정. 'interactive' 지도만 사용
    그림은 SVG(한 장짜리, 우리 서버에 저장) 또는 타일(조각 그림, 연구소·미궁·쇄빙선)"""
    for group in _load_views():
        if group.get("normalizedName") != normalized_name:
            continue
        for m in group.get("maps", []):
            if m.get("projection") == "interactive" and (m.get("svgPath") or m.get("tilePath")):
                svg_name = m["svgPath"].rsplit("/", 1)[-1] if m.get("svgPath") else None
                return {
                    "svg": f"/map-svg/{svg_name}" if svg_name else None,  # 우리 서버를 거쳐 저장본으로
                    "tiles": None if svg_name else m["tilePath"],
                    "tile_size": int(m.get("tileSize") or 256),
                    "svg_layer": m.get("svgLayer"),
                    "transform": m.get("transform"),
                    "rotation": m.get("coordinateRotation") or 0,
                    "bounds": m.get("bounds"),
                    "svg_bounds": m.get("svgBounds"),
                    "min_zoom": m.get("minZoom", 1),
                    "max_zoom": m.get("maxZoom", 5),
                    "author": m.get("author"),
                    "author_link": m.get("authorLink"),
                    # 층: 이름, SVG 안의 층 그림 이름 또는 층 타일, 높이 범위·건물 범위(이 안에 있으면 그 층)
                    "layers": [
                        {
                            "name": layer.get("name"),
                            "svg_layer": layer.get("svgLayer"),
                            "tiles": layer.get("tilePath"),
                            "extents": layer.get("extents") or [],
                        }
                        for layer in m.get("layers") or []
                    ],
                }
    return None


def svg_file(name: str) -> Path | None:
    """지도 그림 파일 (처음 한 번만 받아서 저장). 이름이 이상하면 None"""
    if not SAFE_SVG_NAME.match(name):
        return None
    path = SVG_DIR / name
    if not path.exists():
        data = _download(SVG_HOST + name)
        SVG_DIR.mkdir(parents=True, exist_ok=True)
        from xml.etree import ElementTree

        root = ElementTree.fromstring(data)
        if root.tag.rsplit("}", 1)[-1] != "svg":
            raise ValueError("invalid-svg")
        temp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp.write_bytes(data)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
    return path


def map_list() -> list[dict]:
    """지도 화면에서 고를 수 있는 맵 (뺀 맵 제외, 합친 맵은 하나로)
    [{"key": "customs", "name": "세관", "ids": [지도 id들]}]"""
    from app.quests import HIDDEN_MAPS, MERGED_MAPS  # 맵 정리 규칙은 한 곳(quests.py)에서만

    tarkov_api.update_all()
    maps = tarkov_api.load("maps")["maps"]
    tr = tarkov_api.Translator("maps")
    names = {m["normalizedName"]: tr(m["name"]) for m in maps.values()}
    result = {}
    boss_chances = {}
    boss_members = {}  # 함께 나오는 보스 팀 (군즈) → 멤버 이름
    for m in maps.values():
        key = m["normalizedName"]
        if key in HIDDEN_MAPS:
            continue
        key = MERGED_MAPS.get(key, key)
        item = result.setdefault(key, {"key": key, "name": names.get(key, key), "ids": []})
        item["ids"].append(m["id"])
        chances = boss_chances.setdefault(key, {})
        for boss in m.get("bosses") or []:
            mob = boss.get("mob") or ""
            if not (mob.startswith("boss") or mob == "sectantPriest"):
                continue
            name = tr(mob)
            # 군즈: Knight 가 Big Pipe·Birdeye 와 같이 나오면 한 팀으로 (쇄빙선 Knight 는 혼자라 그대로)
            escorts = {e.get("mob") for e in boss.get("escorts") or [] if isinstance(e, dict)}
            if mob == "bossKnight" and GOONS_ESCORTS <= escorts:
                name = i18n.t("home.goons")
                boss_members[name] = [tr(mob)] + [tr(x) for x in sorted(GOONS_ESCORTS)]
            chance = boss.get("spawnChance")
            if not isinstance(chance, (int, float)) or not 0 <= chance <= 1:
                continue
            chances[name] = max(chances.get(name, 0), chance)
    for key, item in result.items():
        item["bosses"] = [
            {"name": name, "spawnChance": chance, "members": boss_members.get(name, [])}
            for name, chance in sorted(boss_chances[key].items(), key=lambda x: (-x[1], x[0]))
        ]
    return sorted(result.values(), key=lambda x: x["name"])


# 지금은 화면에서 안 씀: 필요템 지도 표시를 없앰 (2026-11-25 사용자). 나중에 다시 쓸 수 있어 남겨 둠
def loose_spots(map_key: str, item_ids: set[str]) -> dict[str, list]:
    """이 맵에서 그 아이템들이 바닥에 생기는 위치 (tarkov.dev maps 의 lootLoose).
    귀중품 위주 350종만 들어 있고, 상자 안에서 나오는 아이템은 없음.
    돌려주는 값: 아이템 id → [[x, z], ...]"""
    from app.quests import MERGED_MAPS

    result: dict[str, list] = {}
    for m in tarkov_api.load("maps")["maps"].values():
        if MERGED_MAPS.get(m["normalizedName"], m["normalizedName"]) != map_key:
            continue
        for loose in m.get("lootLoose") or []:
            pos = loose.get("position") or {}
            for item_id in loose.get("items") or []:
                if item_id in item_ids:
                    result.setdefault(item_id, []).append(_xzy(pos))
    return result


GOONS_ESCORTS = {"followerBigPipe", "followerBirdEye"}  # Knight 와 같이 나오는 군즈 멤버
BITCOIN_ID = "59faff1d86f7746c51718c9c"  # 현물 비트코인
KEY_NAME = re.compile(r"\bkey\b|keycard|key card", re.I)  # 영어 이름으로 열쇠·키카드 구분


def _xzy(position: dict) -> list[float]:
    return [round(position.get("x", 0), 2), round(position.get("z", 0), 2), round(position.get("y", 0), 2)]


def _manual_btr_stops() -> dict:
    """공통 보완 파일의 BTR 정류장 (메타데이터 제외)."""
    return corrections.section("btr_stops")


def _rows(value) -> list:
    return value if isinstance(value, list) else []


def _spawn_positions(value) -> list[tuple]:
    """손상·누락 좌표는 제외하고, 중복 후보 위치는 한 번만 센다."""
    result = []
    seen = set()
    for p in _rows(value):
        if not isinstance(p, dict) or not all(
                type(p.get(axis)) in (int, float) and math.isfinite(p[axis])
                for axis in ("x", "y", "z")):
            continue
        pos = (p["x"], p["z"], p["y"])
        if pos not in seen:
            seen.add(pos)
            result.append(pos)
    return result


def _spawn_center(positions: list) -> list:
    return [round(sum(p[i] for p in positions) / len(positions), 2) for i in range(3)]


# 배 안은 지금 지도(타일)로 자리를 확인할 수 없어 보스 구역을 그리지 않는다 (story.md 쇄빙선과 같은 이유)
NO_BOSS_ZONE_MAPS = {"icebreaker"}


def _zone_kind(mob: str) -> str:
    """지도 표시 묶음: 군즈(Knight 무리) / 컬티스트 / 그 밖의 보스 (2026-11-26 사용자: 셋으로 나눠 켜고 끔)"""
    return {"bossKnight": "goons", "sectantPriest": "cultist"}.get(mob, "boss")


def _is_zone_boss(mob) -> bool:
    return isinstance(mob, str) and (mob.startswith("boss") or mob == "sectantPriest") and "infected" not in mob.lower()


def _enemy_features(map_key: str, bosses, translate) -> dict:
    """BD 인원은 위치 이름별 합계. 보스·군즈·컬티스트는 확률 없이 위치만 반환.

    보스 구역은 그 보스가 나오는 구역의 자리를 모두 알 때만 내보낸다 (bosses). 일부만 알면 pending 으로:
    세관 레샬라처럼 세 곳 중 한 곳만 그리면 '거기에만 나온다'로 잘못 읽힌다. 편집 도구는 pending 도 보여 준다."""
    bd = {}
    dev_zones = {}
    mob_zones = {}  # 보스 → dev 가 적은 등장 구역 이름들
    for entry in _rows(bosses):
        if not isinstance(entry, dict):
            continue
        mob = entry.get("mob")
        if mob != "blackDivision" and not (_is_zone_boss(mob) and map_key not in NO_BOSS_ZONE_MAPS):
            continue  # vsRF / vsRFSniper 등은 표시하지 않음
        low = high = 1
        count_known = isinstance(entry.get("escorts"), list)
        for escort in _rows(entry.get("escorts")):
            amounts = _rows(escort.get("amount")) if isinstance(escort, dict) else []
            counts = [a["count"] for a in amounts if isinstance(a, dict)
                      and type(a.get("count")) is int and a["count"] >= 0]
            if not counts or len(counts) != len(amounts):
                count_known = False
                continue
            low += min(counts)
            high += max(counts)
        # 같은 항목에 같은 위치 이름이 반복되어도 인원은 한 번만 더한다.
        locations = {}
        for location in _rows(entry.get("spawnLocations")):
            if not isinstance(location, dict):
                continue
            name = location.get("name")
            if not isinstance(name, str) or not name:
                continue
            locations.setdefault(name, {}).update(dict.fromkeys(_spawn_positions(location.get("positions"))))
        for name, positions in locations.items():
            if mob == "blackDivision":
                group = bd.setdefault(name, {"positions": {}, "low": 0, "high": 0, "known": True})
                group["positions"].update(positions)
                group["low"] += low
                group["high"] += high
                group["known"] &= count_known
            else:
                group = dev_zones.setdefault(name, {"positions": {}, "bosses": []})
                group["positions"].update(positions)
                if mob not in group["bosses"]:
                    group["bosses"].append(mob)
                mob_zones.setdefault(mob, set()).add(name)
    overrides = corrections.manual_enemy_spawns(map_key)
    applied = {}
    for name, override in overrides.items():
        group = bd.get(name)
        if group is None and override.get("manual") is True:
            group = bd.setdefault(name, {"positions": {}})
        if group is None:
            continue  # dev에 없는 이름은 추가하지 않는다
        # 게임 확인값만 나중에 추가된 dev 좌표보다 우선한다. 나머지는 좌표 누락 때만 쓴다.
        if group["positions"] and override["from"] != "game":
            continue
        group.update(positions={tuple(_xzy(override)): None}, low=override["count"][0],
                     high=override["count"][1], known=True, source=override["from"],
                     inside=override.get("inside", False), estimated=override.get("estimated", False))
        applied[name] = override
    for name, override in applied.items():
        target_name = override.get("merge_into")
        if not target_name or target_name not in bd:
            continue  # 병합 대상이 없어져도 원래 구역을 유지한다
        group, target = bd[name], bd[target_name]
        if not target["positions"]:
            continue
        target["low"] += group["low"]
        target["high"] += group["high"]
        target["known"] &= group["known"]
        target["estimated"] = target.get("estimated", False) or group.get("estimated", False)
        del bd[name]  # 위치는 병합 대상 그대로 두고 인원만 더한다
    result = {"blackDivision": [], "bossZones": []}
    for name, group in bd.items():
        positions = list(group["positions"])
        if not positions:
            continue
        result["blackDivision"].append({
            "name": name, "pos": _spawn_center(positions), "spots": len(positions),
            "count": [group["low"], group["high"]] if group["known"] else None,
            **{key: group[key] for key in ("source", "inside", "estimated") if key in group},
        })
    if map_key in NO_BOSS_ZONE_MAPS:
        return result
    zones = []
    for name, group in dev_zones.items():
        positions = list(group["positions"])
        if not positions:
            continue
        center = _spawn_center(positions)
        # dev 좌표 전체를 감싸는 반경 (한 지점이면 원 대신 핀만 표시).
        radius = max(math.hypot(p[0] - center[0], p[1] - center[1]) for p in positions)
        zones.append(({"name": name, "pos": center, "radius": round(radius, 2), "dev": True}, group["bosses"]))
    valid_bosses = {b.get("mob") for b in _rows(bosses) if isinstance(b, dict) and _is_zone_boss(b.get("mob"))}
    filled = set()   # 우리가 자리를 채운 dev 구역 이름 (위키로 잰 터미널 구역)
    extra = {}       # 보스 → 직접 찍은 자리 수 (편집 도구의 manual_N)
    for zone in corrections.manual_boss_zones(map_key):
        # 같은 등장 구역에 dev 좌표가 하나라도 있으면 그 구역의 보완 좌표를 쓰지 않는다.
        if dev_zones.get(zone["spawn_location"], {}).get("positions"):
            continue
        manual = zone["spawn_location"].startswith("manual_")
        remaining = [mob for mob in zone["bosses"] if mob in valid_bosses]
        if (map_key != "terminal" and not manual) or not remaining:
            continue
        if manual:
            for mob in remaining:
                extra[mob] = extra.get(mob, 0) + 1
        else:
            filled.add(zone["spawn_location"])
        zones.append(({"name": zone["spawn_location"], "pos": _xzy(zone), "radius": zone["radius"],
                       "source": zone["source"]}, remaining))

    moves = corrections.boss_zone_moves(map_key)

    def complete(mob):
        # dev 가 자리를 안 적은 구역 수만큼 직접 찍은 자리가 있어야 그 보스를 지도에 보인다
        missing = [name for name in mob_zones.get(mob, ()) if not dev_zones[name]["positions"] and name not in filled]
        return len(missing) <= extra.get(mob, 0)

    def label(mob):
        return i18n.t("home.goons") if mob == "bossKnight" else translate(mob)

    for base, mobs in zones:
        for kind in ("boss", "goons", "cultist"):
            mine = [mob for mob in mobs if _zone_kind(mob) == kind]
            shown = [label(mob) for mob in mine if complete(mob)]
            pending = [label(mob) for mob in mine if not complete(mob)]
            if not (shown or pending):
                continue
            item = {**base, "kind": kind, "bosses": shown, "pending": pending}
            # dev 구역을 게임에서 확인한 자리로 옮겼거나 숨긴 보정 (dev 는 좌표 여러 개의 가운데라 엉뚱한 곳일 수 있음: 삼림 ZoneWoodCutter)
            move = moves.get(f"{base['name']}_{kind}") if base.get("dev") else None
            if move:
                item.update(pos=[move["x"], move["z"], move["y"]], moved=True)
                if move["hidden"]:
                    item["hidden"] = True  # 화면은 편집 도구에서만 보임 (다시 보이게 할 수 있게)
            result["bossZones"].append(item)
    return result


def features(map_key: str) -> dict:
    """지도에 같이 표시할 것들 (tarkov.dev maps).
    - extracts : 탈출구 (영어 이름, pmc/scav/shared)
    - transits : 트랜짓 (영어 이름)
    - locks    : 열쇠로 여는 문·트렁크 (필요한 열쇠 한국어 이름)
    - loot     : 귀중품이 바닥에 생기는 곳 (그 자리 아이템 이름 몇 개)
    합친 맵(야간 공장 등)은 기본 맵(낮) 것만 씀"""
    from app.quests import MERGED_MAPS

    maps = tarkov_api.load("maps")["maps"]
    m = next((x for x in maps.values() if x["normalizedName"] == map_key), None)
    if not m:
        return {"extracts": [], "transits": [], "locks": [], "loot": [], "btrStops": [],
                "blackDivision": [], "bossZones": []}
    en = tarkov_api.load("maps_en")
    items = tarkov_api.Translator("items")
    map_names = tarkov_api.Translator("maps")

    def item_name(item_id: str) -> str:
        name = items(f"{item_id} Name")
        return "" if name.endswith(" Name") else name

    result = {"extracts": [], "transits": [], "locks": [], "loot": [], "btrStops": []}
    result.update(_enemy_features(map_key, m.get("bosses"), map_names))
    for stop in m.get("btrStops") or []:
        result["btrStops"].append({
            "name": map_names(stop.get("name") or ""),
            "pos": _xzy(stop),
        })
    if not result["btrStops"]:
        # tarkov.dev 에 아직 없는 맵(등대)은 직접 적은 정류장 (위키 그림 기준). dev 에 생기면 dev 가 이김
        manual = _manual_btr_stops().get(map_key) or {}
        for stop in manual.get("stops") or []:
            result["btrStops"].append({"name": stop["name"], "pos": _xzy(stop), "source": manual.get("source", "manual")})
    known_stops = {stop["name"] for stop in result["btrStops"]}
    for stop in corrections.section("btr_stops").get(map_key, {}).get("stops", []):
        if stop.get("from") == "game" and stop.get("name") not in known_stops:
            result["btrStops"].append({"name": stop["name"], "pos": _xzy(stop), "source": "game"})
            known_stops.add(stop["name"])
    for e in m.get("extracts") or []:
        if e.get("position"):
            result["extracts"].append({
                "name": en.get(e.get("name"), e.get("name")),
                "faction": e.get("faction", "shared"),
                "pos": _xzy(e["position"]),
            })
    # tarkov.dev 에 아직 없는 탈출구 (등대 V-Ex 등, 위키 기준). dev 에 생기면 dev 가 이김
    for e in corrections.manual_extracts(map_key, m.get("extracts")):
        result["extracts"].append({
            "name": e["name_en"],
            "faction": e["faction"],
            "pos": _xzy(e["position"]),
            "source": e["source"],
        })
    # 트랜짓: 도착 맵(id·이름)도 같이 → 지도에는 도착 맵 이름, 서랍에는 '트랜짓으로 갈 수 있는 맵'
    by_id = {x["id"]: x for x in maps.values()}
    for tr in m.get("transits") or []:
        if tr.get("position"):
            target = by_id.get(tr.get("map") or "")
            result["transits"].append({
                "name": en.get(tr.get("description"), tr.get("description")),
                "to": tr.get("map") or "",
                # 변형 맵의 정확한 이름 (연구소 (Dark) 등). PvE 에서 합친 맵(그라운드 제로 21+ 등)은 보내지 않음 → 합친 맵 이름으로 보임
                "to_name": map_names(target["name"]) if target and target.get("name")
                and target.get("normalizedName") not in MERGED_MAPS else "",
                "pos": _xzy(tr["position"]),
            })
    for lock in m.get("locks") or []:
        if lock.get("position"):
            result["locks"].append({
                "key": item_name(lock.get("key", "")) or "열쇠",
                "key_id": lock.get("key"),
                "wiki": tarkov_api.key_wiki(lock["key"]) if lock.get("key") else None,  # 마우스를 올리면 뜨는 창의 📖 링크
                "type": lock.get("lockType", "door"),
                "power": bool(lock.get("needsPower")),
                "pos": _xzy(lock["position"]),
            })
    # 바닥 아이템 자리: 비트코인 / 열쇠(키카드 포함)만 (기타 귀중품 표시는 2026-11-23 사용자 결정으로 없앰)
    for loose in m.get("lootLoose") or []:
        if not loose.get("position"):
            continue
        ids = loose.get("items") or []
        key_ids = [i for i in ids if KEY_NAME.search(items.en.get(f"{i} Name", "")) and item_name(i)]
        if BITCOIN_ID not in ids and not key_ids:
            continue
        result["loot"].append({
            "pos": _xzy(loose["position"]),
            "bitcoin": BITCOIN_ID in ids,
            "keys": [item_name(i) for i in key_ids],
            "keyWikis": [tarkov_api.key_wiki(i) for i in key_ids],  # keys 와 같은 순서
        })
    from app import editor
    return editor.feature_keys(map_key, result)

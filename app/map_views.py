"""지도 그림과 좌표 맞추는 값 (지도 화면용).

- 좌표 값: tarkov.dev 웹사이트 코드의 src/data/maps.json (MIT 라이선스)
  맵마다 SVG 그림 주소, transform(배율·이동), coordinateRotation(회전), bounds(범위), 그린 사람
- 지도 그림: the-hideout/tarkov-dev-svg-maps (CC BY-NC-SA 4.0 → 비상업 + 출처 표시)
- 둘 다 cache/ 폴더에 저장해 두고, 좌표 값은 하루에 한 번만 새로 확인한다.
  (인터넷이 안 되거나 받기에 실패하면 저장본을 그대로 씀)
"""

import json
import re
import threading
import time
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


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "RaidBriefer"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read()


def _load_views() -> list:
    """tarkov.dev 의 지도 설정 목록 (저장본이 하루 넘게 지났으면 새로 받기)"""
    with _lock:
        fresh = VIEWS_FILE.exists() and time.time() - VIEWS_FILE.stat().st_mtime < CHECK_SECONDS
        if not fresh:
            try:
                data = _download(VIEWS_URL)
                json.loads(data)  # 깨진 파일은 저장하지 않음
                CACHE_DIR.mkdir(exist_ok=True)
                VIEWS_FILE.write_bytes(data)
            except Exception:
                if not VIEWS_FILE.exists():
                    raise
                VIEWS_FILE.touch()  # 실패해도 저장본으로 하루 버팀 (매번 다시 시도하지 않게)
        return json.loads(VIEWS_FILE.read_text(encoding="utf-8"))


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
        temp = path.with_suffix(".tmp")
        temp.write_bytes(data)
        temp.replace(path)
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


def features(map_key: str) -> dict:
    """지도에 같이 표시할 것들 (tarkov.dev maps).
    - extracts : 탈출구 (영어 이름, pmc/scav/shared)
    - transits : 트랜짓 (영어 이름)
    - locks    : 열쇠로 여는 문·트렁크 (필요한 열쇠 한국어 이름)
    - loot     : 귀중품이 바닥에 생기는 곳 (그 자리 아이템 이름 몇 개)
    합친 맵(야간 공장 등)은 기본 맵(낮) 것만 씀"""
    maps = tarkov_api.load("maps")["maps"]
    m = next((x for x in maps.values() if x["normalizedName"] == map_key), None)
    if not m:
        return {"extracts": [], "transits": [], "locks": [], "loot": [], "btrStops": []}
    en = tarkov_api.load("maps_en")
    items = tarkov_api.Translator("items")
    map_names = tarkov_api.Translator("maps")

    def item_name(item_id: str) -> str:
        name = items(f"{item_id} Name")
        return "" if name.endswith(" Name") else name

    result = {"extracts": [], "transits": [], "locks": [], "loot": [], "btrStops": []}
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
    for e in m.get("extracts") or []:
        if e.get("position"):
            result["extracts"].append({
                "name": en.get(e.get("name"), e.get("name")),
                "faction": e.get("faction", "shared"),
                "pos": _xzy(e["position"]),
            })
    for tr in m.get("transits") or []:
        if tr.get("position"):
            result["transits"].append({
                "name": en.get(tr.get("description"), tr.get("description")),
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
    # 바닥 아이템 자리를 셋으로 나눔: 비트코인 / 열쇠(키카드 포함) / 기타 귀중품
    for loose in m.get("lootLoose") or []:
        if not loose.get("position"):
            continue
        ids = loose.get("items") or []
        key_ids = [i for i in ids if KEY_NAME.search(items.en.get(f"{i} Name", "")) and item_name(i)]
        others = [item_name(i) for i in ids if i != BITCOIN_ID and not KEY_NAME.search(items.en.get(f"{i} Name", ""))]
        others = [n for n in others if n]
        result["loot"].append({
            "pos": _xzy(loose["position"]),
            "bitcoin": BITCOIN_ID in ids,
            "keys": [item_name(i) for i in key_ids],
            "keyWikis": [tarkov_api.key_wiki(i) for i in key_ids],  # keys 와 같은 순서
            "items": others[:4],
            "more": max(0, len(others) - 4),
        })
    return result

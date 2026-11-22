"""Source-only position editor. Signed targets, lossless JSON edits and backups."""

from datetime import date
from contextlib import contextmanager
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import threading

from app import paths

_enabled = False
_secret = secrets.token_bytes(32)
_lock = threading.Lock()
_MAP = re.compile(r"[a-z0-9][a-z0-9_-]{0,59}")
_created = set()
_add_context = {}
_DELETE = object()
_KINDS = {"key_spawn", "panel", "toolset", "explosive", "door", "lock", "building", "start"}


def _identity(file, route, entry):
    name = route[-1] if isinstance(route[-1], str) else entry.get("spawn_location", entry.get("name"))
    if name is None:  # 이름 없는 목록 항목(스토리 장소)은 단계·좌표로 구분
        name = (entry.get("map"), entry.get("x"), entry.get("z"))
    return (file, tuple(route[:-1]), name)


def enable():
    global _enabled
    _enabled = not getattr(sys, "frozen", False)


def enabled():
    return _enabled and not getattr(sys, "frozen", False)


def session_id():
    return hashlib.sha256(_secret).hexdigest()


def _target(file, route):
    """Only these exact data files and entry shapes can ever be written."""
    if not isinstance(file, str) or not isinstance(route, list):
        raise ValueError("target")
    if file == "corrections.json":
        if len(route) < 3 or not isinstance(route[1], str) or not _MAP.fullmatch(route[1]):
            raise ValueError("map")
        sections = {"extracts": "extracts", "btr_stops": "stops", "boss_zones": "zones"}
        if route[0] in sections:
            if len(route) != 4 or route[2] != sections[route[0]] or type(route[3]) is not int or route[3] < 0:
                raise ValueError("index")
        elif route[0] != "enemy_spawns" or len(route) != 3 or not isinstance(route[2], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", route[2]):
            raise ValueError("spawn")
    elif file == "story/places.json" and route[:1] == ["step_maps"]:
        if len(route) != 2 or not isinstance(route[1], str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", route[1]):
            raise ValueError("step_maps")
    elif file == "story/places.json" and route[:1] == ["no_place"]:
        if len(route) != 2 or type(route[1]) is not int or route[1] < 0:
            raise ValueError("no_place")
    elif file == "story/places.json":
        if (len(route) != 3 or route[0] != "places" or not isinstance(route[1], str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", route[1]) or type(route[2]) is not int or route[2] < 0):
            raise ValueError("place")
    elif re.fullmatch(r"guides/[a-z0-9][a-z0-9_-]{0,59}\.json", file):
        if len(route) != 2 or route[0] != "points" or not isinstance(route[1], str) or not route[1]:
            raise ValueError("point")
    else:
        raise ValueError("file")
    root = paths.DATA_DIR.resolve()
    target = root / file
    # Refuse symlinks/junctions leading outside the data folder.
    if target.resolve() != target or not target.is_file():
        raise ValueError("file")
    return target


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate-key")
        result[key] = value
    return result


def _read(path):
    raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    if not isinstance(data, dict):
        raise ValueError("object")
    return raw, data


def _entry(data, route):
    for key in route:
        if isinstance(data, dict) and isinstance(key, str):
            data = data.get(key)
        elif isinstance(data, list) and type(key) is int and 0 <= key < len(data):
            data = data[key]
        else:
            return None
    return data


def _number(value):
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _signature(key):
    # JS JSON.stringify turns -59.0 into -59; both must verify as the same number.
    def canonical(value):
        if isinstance(value, dict):
            return {k: canonical(v) for k, v in value.items()}
        if isinstance(value, list):
            return [canonical(v) for v in value]
        if type(value) is float and math.isfinite(value) and value.is_integer():
            return int(value)
        return value

    return hmac.new(_secret, json.dumps(canonical(key), sort_keys=True, ensure_ascii=False,
                                      separators=(",", ":"), allow_nan=False).encode("utf-8"), hashlib.sha256).hexdigest()


def _attach(item, file, route, snapshot, seed=None):
    _target(file, route)
    raw, data = snapshot
    entry = _entry(data, route)
    if entry is not None:
        if not isinstance(entry, dict) or not all(_number(entry.get(k)) for k in ("x", "z")) or not _number(entry.get("y", 0)):
            return
        item["pos"] = [entry["x"], entry["z"], entry.get("y", 0)]
        # 스토리 장소는 우리 데이터 전체를 편집 도구로 고침 → 모두 삭제 가능
        seed = {"created": True} if file == "story/places.json" or _identity(file, route, entry) in _created else None
    elif seed is None:
        return
    key = {"file": file, "path": route, "revision": hashlib.sha256(raw).hexdigest(), "seed": seed}
    item["edit"] = {**key, "signature": _signature(key)}


def guide_keys(guide):
    if not enabled():
        return guide
    file = "guides/" + guide["map"] + ".json"
    try:
        snapshot = _read(_target(file, ["points", "_"]))
        for key, point in guide["points"].items():
            _attach(point, file, ["points", key], snapshot)
    except (OSError, ValueError, RecursionError):
        pass  # Broken/missing files remain read-only.
    return guide


def _story_steps(map_key):
    """이 맵이 적힌 스토리 단계 목록 (챕터 이름·한/영 글). 편집자가 고친 맵 목록(step_maps)을 따름"""
    from app import story_places
    overrides = story_places.step_maps()
    return [{"id": sid, "chapter": s["chapter"], "ko": s["ko"], "en": s["en"]}
            for sid, s in _step_index().items() if map_key in overrides.get(sid, s["maps"])]


def _step_index():
    """모든 스토리 단계 {id: 원래 맵 목록·챕터·글} (단계에 맵이 없으면 구간 맵)"""
    steps = {}
    for path in sorted((paths.DATA_DIR / "story").glob("*.json")):
        if path.name == "places.json":
            continue
        try:
            chapter = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(chapter, dict) or not isinstance(chapter.get("sections"), list):
            continue
        name = chapter.get("name", path.stem)
        # 화면(스토리 탭)과 같은 규칙: 단계에 맵이 없으면 그 구간의 맵을 물려받음
        for section in chapter["sections"]:
            if not isinstance(section, dict) or not isinstance(section.get("steps"), list):
                continue
            for step in section["steps"]:
                if not isinstance(step, dict) or not isinstance(step.get("id"), str):
                    continue
                maps = step.get("map") or section.get("map")
                maps = [m for m in (maps if isinstance(maps, list) else [maps]) if isinstance(m, str)]
                steps[step["id"]] = {"maps": maps, "chapter": name, "ko": step.get("ko", ""), "en": step.get("en", "")}
    return steps


def story_keys(places):
    """스토리 장소마다 편집 키 (편집 모드일 때만). 파일의 실제 순번과 맞춘다"""
    if not enabled():
        return places
    from app import story_places
    try:
        snapshot = _read(_target("story/places.json", ["places", "x", 0]))
        raw_places = snapshot[1].get("places", {})
        result = {}
        for sid, entries in places.items():
            rows = raw_places.get(sid) if isinstance(raw_places, dict) else None
            valid_index = [i for i, row in enumerate(rows or []) if story_places.valid(row)]
            copies = []
            for k, point in enumerate(entries):
                item = dict(point)
                if k < len(valid_index):
                    _attach(item, "story/places.json", ["places", sid, valid_index[k]], snapshot)
                copies.append(item)
            result[sid] = copies
        return result
    except (OSError, ValueError, RecursionError):
        return places


def feature_keys(map_key, result):
    if not enabled() or not _MAP.fullmatch(map_key):
        return result
    try:
        snapshot = _read(_target("corrections.json", ["enemy_spawns", map_key, "Editor"]))
        data = snapshot[1]
        result["editorAdd"] = _add_keys(map_key, result, snapshot)
        for group, section, rows, name_field in (
                ("extracts", "extracts", "extracts", "name_en"),
                ("btrStops", "btr_stops", "stops", "name"),
                ("bossZones", "boss_zones", "zones", "spawn_location")):
            entries = _entry(data, [section, map_key, rows])
            if not isinstance(entries, list):
                continue
            for item in result.get(group, []):
                if "source" not in item:
                    continue  # dev markers never acquire manual edit keys.
                matches = [i for i, row in enumerate(entries) if isinstance(row, dict)
                           and (row.get(name_field) or row.get("name")) == item.get("name")]
                if len(matches) == 1:
                    _attach(item, "corrections.json", [section, map_key, rows, matches[0]], snapshot)
        for item in result.get("blackDivision", []):
            counts = item.get("count")
            seed = None
            if isinstance(counts, list) and len(counts) == 2 and all(type(n) is int and n >= 1 for n in counts) and counts[0] <= counts[1]:
                seed = {"y": item["pos"][2], "count": counts, "note": "position set in editor"}
            _attach(item, "corrections.json", ["enemy_spawns", map_key, item["name"]], snapshot, seed)
    except (OSError, ValueError, RecursionError):
        pass
    return result


def _add_keys(map_key, result, snapshot):
    from app import tarkov_api
    maps = tarkov_api.load("maps").get("maps", {})
    base = next((m for m in maps.values() if isinstance(m, dict) and m.get("normalizedName") == map_key), {})
    bosses = sorted({b["mob"] for b in base.get("bosses") or []
                     if isinstance(b, dict) and isinstance(b.get("mob"), str)
                     and (b["mob"].startswith("boss") or b["mob"] == "sectantPriest")
                     and "infected" not in b["mob"].lower()})
    heights = [item["pos"] for rows in result.values() if isinstance(rows, list)
               for item in rows if isinstance(item, dict) and isinstance(item.get("pos"), list)
               and len(item["pos"]) == 3 and all(_number(n) for n in item["pos"])]
    guide = None
    try:
        guide = _read(_target("guides/" + map_key + ".json", ["points", "_"]))
        if guide[1].get("map") != map_key or not isinstance(guide[1].get("points"), dict) or not isinstance(guide[1].get("steps"), list):
            raise ValueError("guide")
        heights.extend([p["x"], p["z"], p["y"]] for p in guide[1].get("points", {}).values()
                       if isinstance(p, dict) and all(_number(p.get(k)) for k in ("x", "y", "z")))
    except (OSError, ValueError):
        guide = None
    result_keys = {}
    result["editorHeights"] = heights
    story = None
    try:
        story = _read(_target("story/places.json", ["places", "x", 0]))
    except (OSError, ValueError):
        story = None
    story_steps = _story_steps(map_key) if story is not None else []
    for kind in ("bd", "boss", "extract", "btr", "guide", "story", "noplace", "maps", "unnoplace"):
        if kind == "guide" and guide is None:
            continue
        if kind in ("story", "noplace") and not story_steps:
            continue
        if kind in ("maps", "unnoplace") and story is None:
            continue
        file = {"guide": "guides/" + map_key + ".json", "story": "story/places.json",
                "noplace": "story/places.json", "maps": "story/places.json",
                "unnoplace": "story/places.json"}.get(kind, "corrections.json")
        raw, data = {"guide": guide, "story": story, "noplace": story, "maps": story, "unnoplace": story}.get(kind) or snapshot
        route = {"guide": ["points", "_"], "story": ["places", "x", 0],
                 "noplace": ["no_place", 0], "maps": ["step_maps", "x"],
                 "unnoplace": ["no_place", 0]}.get(kind, ["enemy_spawns", map_key, "Editor"])
        context = hashlib.sha256(json.dumps([map_key, heights], separators=(",", ":")).encode()).hexdigest()
        _add_context[context] = heights
        # Keep old tabs usable without unbounded growth in long editing sessions.
        while len(_add_context) > 128:
            del _add_context[next(iter(_add_context))]
        seed = {"add": kind, "map": map_key, "bosses": bosses, "context": context}
        if kind in ("extract", "btr"):
            seed["names"] = [item.get("name") for item in result.get("extracts" if kind == "extract" else "btrStops", [])]
        if kind == "noplace":
            seed["steps"] = [s["id"] for s in story_steps]
        if kind == "story":
            seed["steps"] = story_steps
            seed["placed"] = sorted(sid for sid, rows in (data.get("places") or {}).items()
                                    if isinstance(rows, list) and any(isinstance(r, dict) and r.get("map") == map_key for r in rows))
        if kind == "guide":
            seed["steps"] = [{"id": s["id"], "ko": s.get("ko", ""), "en": s.get("en", "")}
                             for s in data.get("steps", []) if isinstance(s, dict) and isinstance(s.get("id"), str)]
        key = {"file": file, "path": route, "revision": hashlib.sha256(raw).hexdigest(), "seed": seed}
        result_keys[kind] = {**key, "signature": _signature(key)}
    return result_keys


def _patch(raw, updates):
    """Replace only JSON value spans; preserve all untouched bytes and key order."""
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    newline = "\r\n" if "\r\n" in text else "\n"
    indent_match = re.search(r"(?:\r?\n)([ \t]+)\"", text)
    indent = indent_match[1] if indent_match else "  "
    decoder = json.JSONDecoder()
    nodes = {}

    def whitespace(i):
        while i < len(text) and text[i].isspace():
            i += 1
        return i

    def scan(i, route):
        start = i
        children = {}
        if text[i] in "{[":
            is_object = text[i] == "{"
            closing = "}" if is_object else "]"
            i = whitespace(i + 1)
            while text[i] != closing:
                key = len(children)
                if is_object:
                    key, i = decoder.raw_decode(text, i)
                    i = whitespace(i)
                    if text[i] != ":":
                        raise ValueError("colon")
                    i = whitespace(i + 1)
                i = scan(i, route + (key,))
                children[key] = nodes[route + (key,)]
                i = whitespace(i)
                if text[i] == closing:
                    break
                if text[i] != ",":
                    raise ValueError("comma")
                i = whitespace(i + 1)
            i += 1
        else:
            _, i = decoder.raw_decode(text, i)
        nodes[route] = (start, i, children)
        return i

    scan(whitespace(0), ())
    tree = {}
    for route, value in updates:
        branch = tree
        for key in route[:-1]:
            branch = branch.setdefault(key, {})
        branch[route[-1]] = value
    patches = []

    def dump(value, prefix, multiline):
        def containers(item):
            if isinstance(item, dict):
                if item and all(type(k) is int for k in item):
                    if list(item) != list(range(len(item))):
                        raise ValueError("array")
                    return [containers(v) for v in item.values()]
                return {k: containers(v) for k, v in item.items()}
            return item
        value = containers(value)
        if not multiline:
            return json.dumps(value, ensure_ascii=False, allow_nan=False)
        return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=indent).replace("\n", newline + prefix)

    def visit(route, changes):
        start, end, children = nodes[route]
        missing = {}
        deleted = set()
        for key, value in changes.items():
            child = children.get(key)
            if value is _DELETE:
                if child is None:
                    raise ValueError("missing")
                deleted.add(key)
            elif child is None:
                missing[key] = value
            elif isinstance(value, dict):
                if text[child[0]] not in "{[":
                    raise ValueError("container")
                visit(route + (key,), value)
            else:
                patches.append((child[0], child[1], dump(value, "", False)))
        keys = list(children)
        # Delete consecutive runs including exactly one adjacent separator.
        index = 0
        while index < len(keys):
            if keys[index] not in deleted:
                index += 1
                continue
            first = index
            while index + 1 < len(keys) and keys[index + 1] in deleted:
                index += 1
            last = index
            def member_start(k):
                pos = start + 1 if k == 0 else whitespace(children[keys[k - 1]][1]) + 1
                return whitespace(pos)
            if last + 1 < len(keys):
                left, right = member_start(first), member_start(last + 1)
            elif first:
                left, right = children[keys[first - 1]][1], children[keys[last]][1]
            else:
                left, right = start + 1, end - 1
            patches.append((left, right, ""))
            index += 1
        if missing:
            multiline = "\n" in text[start:end]
            line = text[text.rfind("\n", 0, start) + 1:start]
            parent_indent = re.match(r"[ \t]*", line)[0]
            prefix = parent_indent + indent
            separator = newline + prefix if multiline else " "
            entries = ((json.dumps(key, ensure_ascii=False) + ": " if text[start] == "{" else "") + dump(value, prefix, multiline)
                       for key, value in missing.items())
            pos = end - 1 if deleted else next(reversed(children.values()))[1] if children else start + 1
            added = ("," if len(children) > len(deleted) else "") + separator + ("," + separator).join(entries)
            if not children and multiline:
                added += newline + parent_indent
            patches.append((pos, pos, added))

    visit((), tree)
    for start, end, replacement in sorted(patches, reverse=True):
        text = text[:start] + replacement + text[end:]
    json.loads(text, object_pairs_hook=_pairs)
    return (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")


@contextmanager
def _file_lock(path):
    # OS locks are released even if the console/process is killed during a save.
    with path.open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _invalidate():
    from app import corrections, guides, story_places
    with story_places._lock:
        story_places._cached = None
    # Also work on filesystems with coarse mtime resolution.
    with corrections._lock:
        corrections._cached = None
    with guides._lock:
        guides._cached.clear()


def _add_change(plan, key, change, today):
    seed, fields = key["seed"], change.get("fields")
    kind, map_key = seed["add"], seed["map"]
    if not isinstance(fields, dict):
        raise ValueError("fields")
    allowed = {"note"} | {"bd": {"count"}, "boss": {"bosses"}, "extract": {"name", "faction"},
                          "btr": {"name"}, "guide": {"kind", "label_ko", "label_en", "candidate", "steps"},
                          "story": {"step", "candidate", "y"}, "noplace": {"step"},
                          "maps": {"step", "maps"}, "unnoplace": {"step"}}[kind]
    if set(fields) - allowed:
        raise ValueError("fields")
    def text(field, required=True):
        value = fields.get(field, "")
        if not isinstance(value, str) or len(value) > 1000 or (required and not value.strip()):
            raise ValueError("text")
        return value.strip()
    x, z = round(change["x"], 1), round(change["z"], 1)
    if seed["context"] not in _add_context:
        raise ValueError("stale-context")
    near = min(_add_context[seed["context"]], key=lambda p: (p[0] - x)**2 + (p[1] - z)**2, default=[0, 0, 0])
    entry = {"x": x, "y": near[2], "z": z, "note": text("note", False), "from": "game", "checked": today}
    data = plan["data"]
    index = _step_index() if kind in ("story", "noplace", "maps", "unnoplace") else {}
    if kind == "unnoplace":
        # '장소 불필요'에서 빼기 (장소가 맞는 단계). 원래 파일의 순번으로 지움
        step = fields.get("step")
        rows = data.get("no_place")
        removed = plan.setdefault("unnoplace", set())
        if not isinstance(step, str) or not isinstance(rows, list) or step not in rows or step in removed:
            raise ValueError("unnoplace")
        removed.add(step)
        plan["updates"].append((("no_place", rows.index(step)), _DELETE))
        return
    overrides = data.get("step_maps") if isinstance(data.get("step_maps"), dict) else {}
    if kind == "maps":
        # 단계가 쓰는 맵 목록 바꾸기 (편집자가 여러 맵 체크). 이미 장소가 있는 맵은 뺄 수 없음
        from app import tarkov_api
        step, maps = fields.get("step"), fields.get("maps")
        known = {m.get("normalizedName") for m in tarkov_api.load("maps").get("maps", {}).values() if isinstance(m, dict)}
        if not isinstance(step, str) or step not in index:
            raise ValueError("step")
        if (not isinstance(maps, list) or not 1 <= len(maps) <= 30 or len(set(maps)) != len(maps)
                or not all(isinstance(m, str) and m in known for m in maps)):
            raise ValueError("maps")
        used = {r.get("map") for r in (data.get("places") or {}).get(step, []) if isinstance(r, dict)}
        if used - set(maps):
            raise ValueError("maps-in-use")
        if "step_maps" in data and not isinstance(data["step_maps"], dict):
            raise ValueError("step_maps")
        data.setdefault("step_maps", {})[step] = maps
        plan["updates"].append((("step_maps", step), maps))
        return
    if kind == "noplace":
        # 연속 입력에서 '장소 불필요' (자동 분류가 틀렸을 때 바로잡기)
        step = fields.get("step")
        if not isinstance(step, str) or step not in index:
            raise ValueError("step")
        if (data.get("places") or {}).get(step):
            raise ValueError("has-place")  # 장소가 있는 단계는 '장소 불필요'로 못 넣음 (모순)
        rows = data.setdefault("no_place", [])
        if not isinstance(rows, list) or step in rows:
            raise ValueError("duplicate-no-place")
        route = ["no_place", len(rows)]
        rows.append(step)
        plan["updates"].append((tuple(route), step))
        return
    if kind == "story":
        step = fields.get("step")
        # 단계의 맵: 같은 저장에서 바꾼 맵 목록(step_maps)이 먼저, 없으면 원래 맵
        maps = overrides.get(step) if isinstance(step, str) and isinstance(overrides.get(step), list) else index.get(step, {}).get("maps")
        if not isinstance(step, str) or step not in index or map_key not in (maps or []):
            raise ValueError("step")
        if step in (data.get("no_place") or []) and step not in plan.get("unnoplace", ()):
            raise ValueError("no-place")  # '장소 불필요' 단계에는 장소를 못 넣음 (모순)
        if type(fields.get("candidate", False)) is not bool:
            raise ValueError("candidate")
        # y: 화면에서 고른 층의 높이 (없으면 가장 가까운 표시의 높이)
        y = fields.get("y", near[2])
        if not _number(y) or abs(y) > 10000:
            raise ValueError("y")
        place = {"map": map_key, "x": x, "y": round(y, 2), "z": z, "candidate": fields.get("candidate", False),
                 "note": entry["note"], "from": "game", "checked": today}
        places = data.setdefault("places", {})
        rows = places.setdefault(step, [])
        if any(isinstance(r, dict) and (r.get("map"), r.get("x"), r.get("z")) == (map_key, x, z) for r in rows):
            raise ValueError("duplicate-place")
        route = ["places", step, len(rows)]
        rows.append(place)
        plan["updates"].append((tuple(route), place))
        plan["created"].append(_identity(key["file"], route, place))
        return
    if kind == "guide":
        subtype = fields.get("kind")
        if not isinstance(subtype, str) or subtype not in _KINDS or type(fields.get("candidate")) is not bool:
            raise ValueError("kind")
        entry.update(kind=subtype, label_ko=text("label_ko"), label_en=text("label_en"), candidate=fields["candidate"])
        if re.search(r"[\uac00-\ud7ff]", entry["label_en"]):
            raise ValueError("english")
        container = data["points"]
        route = ["points"]
        prefix = subtype + "_"
    else:
        section, rows = {"bd": ("enemy_spawns", None), "boss": ("boss_zones", "zones"),
                         "extract": ("extracts", "extracts"), "btr": ("btr_stops", "stops")}[kind]
        maps = data.setdefault(section, {})
        if map_key not in maps:
            maps[map_key] = {} if rows is None else {"source": "game", "checked": today, rows: []}
            if rows is not None:
                plan["updates"].extend([((section, map_key, "source"), "game"), ((section, map_key, "checked"), today)])
        container = maps[map_key] if rows is None else maps[map_key][rows]
        route = [section, map_key] + ([rows] if rows else [])
        prefix = "manual_"
    used = set(container) if isinstance(container, dict) else {row.get("spawn_location", row.get("name")) for row in container}
    number = 1
    while prefix + str(number) in used:
        number += 1
    name = prefix + str(number)
    if kind == "bd":
        n = fields.get("count")
        if type(n) is not int or not 1 <= n <= 20:
            raise ValueError("count")
        entry.update(count=[n, n], manual=True)
    elif kind == "boss":
        bosses = fields.get("bosses")
        if not isinstance(bosses, list) or not bosses or any(b not in seed["bosses"] for b in bosses):
            raise ValueError("bosses")
        entry.update(radius=10, bosses=list(dict.fromkeys(bosses)), spawn_location=name)
    elif kind in ("extract", "btr"):
        name = text("name")
        if name in used or name in seed.get("names", []):
            raise ValueError("duplicate-name")
        entry["name"] = name
        if kind == "extract":
            if fields.get("faction") not in ("pmc", "scav", "shared"):
                raise ValueError("faction")
            if re.search(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7ff]", name):
                raise ValueError("english")
            entry.update(name_en=name, faction=fields["faction"])
    route.append(name if isinstance(container, dict) else len(container))
    if isinstance(container, dict):
        container[name] = entry
    else:
        container.append(entry)
    plan["updates"].append((tuple(route), entry))
    plan["created"].append(_identity(key["file"], route, entry))
    if kind == "guide":
        selected = fields.get("steps", [])
        if not isinstance(selected, list) or any(s not in [row["id"] for row in seed["steps"]] for s in selected):
            raise ValueError("steps")
        for index, step in enumerate(data.get("steps", [])):
            if step.get("id") in selected:
                refs = step.setdefault("points", [])
                plan["updates"].append((("steps", index, "points", len(refs)), name))
                refs.append(name)


def save(changes):
    """Validate the entire batch first. Report committed files even on partial IO failure."""
    if not enabled():
        raise PermissionError("disabled")
    if not isinstance(changes, list) or not 1 <= len(changes) <= 500:
        raise ValueError("changes")
    with _lock:
        plans = {}
        seen = set()
        today = date.today().isoformat()
        for change in changes:
            if not isinstance(change, dict) or set(change) not in ({"edit", "x", "z"}, {"edit", "x", "z", "fields"}, {"edit", "x", "z", "delete"}):
                raise ValueError("change")
            key = change["edit"]
            if not isinstance(key, dict) or set(key) != {"file", "path", "revision", "seed", "signature"}:
                raise ValueError("key")
            unsigned = {k: v for k, v in key.items() if k != "signature"}
            if not isinstance(key["signature"], str) or not hmac.compare_digest(key["signature"], _signature(unsigned)):
                raise ValueError("signature")
            path = _target(key["file"], key["path"])
            identity = (key["file"], tuple(key["path"]))
            adding = isinstance(key["seed"], dict) and "add" in key["seed"]
            if (identity in seen and not adding) or not all(_number(change[k]) and abs(change[k]) <= 1000000 for k in ("x", "z")):
                raise ValueError("coordinates")
            seen.add(identity)
            if path not in plans:
                raw, data = _read(path)
                plans[path] = {"raw": raw, "data": data, "updates": [], "keys": [], "created": []}
            plan = plans[path]
            if hashlib.sha256(plan["raw"]).hexdigest() != key["revision"]:
                raise ValueError("stale")
            if adding:
                if "delete" in change:
                    raise ValueError("operation")
                _add_change(plan, key, change, today)
                plan["keys"].append(key)
                continue
            if "fields" in change:
                raise ValueError("operation")
            entry = _entry(plan["data"], key["path"])
            if "delete" in change:
                if (change["delete"] is not True or not isinstance(entry, dict) or key["seed"] != {"created": True}
                        or (key["file"] != "story/places.json" and _identity(key["file"], key["path"], entry) not in _created)):
                    raise ValueError("delete")
                plan["updates"].append((tuple(key["path"]), _DELETE))
                if key["file"].startswith("guides/"):
                    for section in ("prep", "steps"):
                        for index, step in enumerate(plan["data"].get(section, [])):
                            for ref_index, ref in enumerate(step.get("points", [])):
                                if ref == key["path"][-1]:
                                    plan["updates"].append(((section, index, "points", ref_index), _DELETE))
                plan["keys"].append(key)
                continue
            updates = {"x": round(change["x"], 1), "z": round(change["z"], 1), "from": "game", "checked": today}
            if entry is None:
                if key["file"] != "corrections.json" or key["path"][0] != "enemy_spawns" or not isinstance(key["seed"], dict):
                    raise ValueError("missing")
                updates = {**key["seed"], **updates}
            elif not isinstance(entry, dict) or not all(_number(entry.get(k)) for k in ("x", "z")) or not _number(entry.get("y", 0)):
                raise ValueError("position")
            # Existing y, count, merge_into, descriptions and file-level metadata stay byte-for-byte intact.
            plan["updates"].extend((tuple(key["path"]) + (field,), value) for field, value in updates.items())
            if isinstance(entry, dict):
                old_identity = _identity(key["file"], key["path"], entry)
                if old_identity in _created:
                    plan.setdefault("moved", []).append(
                        (old_identity, _identity(key["file"], key["path"], {**entry, **updates})))
            plan["keys"].append(key)
        for plan in plans.values():
            plan["new"] = _patch(plan["raw"], plan["updates"])
        backup = paths.USER_DIR / "_edit_backup"
        backup.mkdir(parents=True, exist_ok=True)
        lock_path = backup / ".saving"
        saved = []
        try:
            with _file_lock(lock_path):
                for path, plan in plans.items():
                    if path.read_bytes() != plan["raw"]:
                        raise ValueError("stale")
                for path, plan in plans.items():
                    with (backup / (path.stem + "-" + secrets.token_hex(12) + ".json")).open("xb") as out:
                        out.write(plan["raw"])
                        out.flush()
                        os.fsync(out.fileno())
                    temp = None
                    try:
                        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".edit-", suffix=".tmp", delete=False) as out:
                            temp = Path(out.name)
                            out.write(plan["new"])
                            out.flush()
                            os.fsync(out.fileno())
                        if path.read_bytes() != plan["raw"]:
                            raise ValueError("stale")
                        os.replace(temp, path)
                        _created.update(plan["created"])
                        for old_identity, new_identity in plan.get("moved", []):
                            _created.discard(old_identity)
                            _created.add(new_identity)
                        saved.extend(plan["keys"])
                    finally:
                        if temp is not None:
                            temp.unlink(missing_ok=True)
        except (OSError, ValueError):
            return {"ok": False, "saved": saved}
        finally:
            if saved:
                _invalidate()
        return {"ok": True, "saved": saved}

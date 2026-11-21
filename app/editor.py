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
        if not isinstance(entry, dict) or not all(_number(entry.get(k)) for k in ("x", "y", "z")):
            return
        item["pos"] = [entry["x"], entry["z"], entry["y"]]
        seed = None
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


def feature_keys(map_key, result):
    if not enabled() or not _MAP.fullmatch(map_key):
        return result
    try:
        snapshot = _read(_target("corrections.json", ["enemy_spawns", map_key, "Editor"]))
        data = snapshot[1]
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
        if not multiline:
            return json.dumps(value, ensure_ascii=False, allow_nan=False)
        return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=indent).replace("\n", newline + prefix)

    def visit(route, changes):
        start, end, children = nodes[route]
        missing = {}
        for key, value in changes.items():
            child = children.get(key)
            if child is None:
                missing[key] = value
            elif isinstance(value, dict):
                if text[child[0]] not in "{[":
                    raise ValueError("container")
                visit(route + (key,), value)
            else:
                patches.append((child[0], child[1], dump(value, "", False)))
        if missing:
            if text[start] != "{":
                raise ValueError("object")
            multiline = "\n" in text[start:end]
            line = text[text.rfind("\n", 0, start) + 1:start]
            parent_indent = re.match(r"[ \t]*", line)[0]
            prefix = parent_indent + indent
            separator = newline + prefix if multiline else " "
            entries = (json.dumps(key, ensure_ascii=False) + ": " + dump(value, prefix, multiline)
                       for key, value in missing.items())
            pos = next(reversed(children.values()))[1] if children else start + 1
            added = ("," if children else "") + separator + ("," + separator).join(entries)
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
    from app import corrections, guides
    # Also work on filesystems with coarse mtime resolution.
    with corrections._lock:
        corrections._cached = None
    with guides._lock:
        guides._cached.clear()


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
            if not isinstance(change, dict) or set(change) != {"edit", "x", "z"}:
                raise ValueError("change")
            key = change["edit"]
            if not isinstance(key, dict) or set(key) != {"file", "path", "revision", "seed", "signature"}:
                raise ValueError("key")
            unsigned = {k: v for k, v in key.items() if k != "signature"}
            if not isinstance(key["signature"], str) or not hmac.compare_digest(key["signature"], _signature(unsigned)):
                raise ValueError("signature")
            path = _target(key["file"], key["path"])
            identity = (key["file"], tuple(key["path"]))
            if identity in seen or not all(_number(change[k]) and abs(change[k]) <= 1000000 for k in ("x", "z")):
                raise ValueError("coordinates")
            seen.add(identity)
            if path not in plans:
                raw, data = _read(path)
                plans[path] = {"raw": raw, "data": data, "updates": [], "keys": []}
            plan = plans[path]
            if hashlib.sha256(plan["raw"]).hexdigest() != key["revision"]:
                raise ValueError("stale")
            entry = _entry(plan["data"], key["path"])
            updates = {"x": round(change["x"], 1), "z": round(change["z"], 1), "from": "game", "checked": today}
            if entry is None:
                if key["file"] != "corrections.json" or key["path"][0] != "enemy_spawns" or not isinstance(key["seed"], dict):
                    raise ValueError("missing")
                updates = {**key["seed"], **updates}
            elif not isinstance(entry, dict) or not all(_number(entry.get(k)) for k in ("x", "y", "z")):
                raise ValueError("position")
            # Existing y, count, merge_into, descriptions and file-level metadata stay byte-for-byte intact.
            plan["updates"].extend((tuple(key["path"]) + (field,), value) for field, value in updates.items())
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

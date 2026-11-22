"""게임 로그(push-notifications)를 읽어서 퀘스트 상태를 알아내는 파일.

로그에 남는 퀘스트 알림 (new_message의 type)
- 10 = 퀘스트 시작
- 11 = 퀘스트 실패
- 12 = 퀘스트 완료

같은 퀘스트에 여러 기록이 있으면 '가장 마지막 기록'이 지금 상태다.
(예: 실패(11) 후 다시 시작(10)하면 → 진행 중)
"""

import heapq
import json
import math
import os
import re
import tempfile
import threading
import time
from bisect import bisect_right
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app import corrections, i18n, paths, settings, tarkov_api


# ---------- 가장 최근 프레스티지(초기화) 찾기 ----------
# 규칙 (어느 PC에서나 같음):
#   backend 로그에서 PvE 서버(gw-pve)로 보낸 "/client/prestige/obtain" 요청 중 가장 최근 시각.
#   찾지 못하면 None → 남아 있는 로그 전체를 처음부터 읽는다.
# 로그 파일이 많아서, 이미 읽은 파일은 기억해 두고 새로 생기거나 바뀐 파일만 다시 읽는다.
PRESTIGE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*Request HTTPS.*https://gw-pve[^/]*/client/prestige/obtain")
_folder_cache = {}
_log_cache_lock = threading.RLock()
_cache_root = None
_index_state = None
_reading_lock = threading.Lock()
_reading_progress = {"active": False, "done": 0, "total": 0, "started": 0.0}
_revision_poll_lock = threading.Lock()
_revision_snapshot = ""
_LOG_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)")
_MODE_REQUEST = re.compile(r"https://(gw-pv[ep][^/.:]*)\.escapefromtarkov\.com(?::\d+)?(/client/[^\s|?]*)")
SEALED_AFTER_NS = 3600 * 10**9
REVALIDATE_SECONDS = 600  # 종료 폴더의 외부 수정은 요청을 막지 않고 10분마다 확인한다.


@contextmanager
def _reading():
    # 색인 잠금과 분리한다. 진행률 잠금 안에서는 파일 접근이나 대기를 하지 않는다.
    with _reading_lock:
        owner = not _reading_progress["active"]
        if owner:
            _reading_progress.update(active=True, done=0, total=0, started=time.monotonic())
    try:
        yield
    finally:
        if owner:
            with _reading_lock:
                _reading_progress["active"] = False


def log_reading():
    """스캔을 기다리지 않는 작은 복사본. 경과 시간은 PC 시계 변경에 영향받지 않는다."""
    with _reading_lock:
        if not _reading_progress["active"]:
            return None
        return {"done": _reading_progress["done"], "total": _reading_progress["total"],
                "elapsed_ms": (time.monotonic() - _reading_progress["started"]) * 1000}


def _advance_log_reading():
    with _reading_lock:
        _reading_progress["done"] += 1


def poll_log_rev():
    """상태 요청에서는 디스크를 읽거나 색인 잠금을 기다리지 않고 마지막 값을 반환한다."""
    def refresh():
        global _revision_snapshot
        try:
            _revision_snapshot = log_rev()
        except (OSError, settings.DataReadError):
            # 일시적인 설정·폴더 접근 실패에는 마지막 확인값을 보존한다.
            pass
        finally:
            _revision_poll_lock.release()

    # 긴 첫 읽기 중에도 대기 스레드는 하나만 유지한다.
    if _revision_poll_lock.acquire(blocking=False):
        try:
            threading.Thread(target=refresh, name="log-revision", daemon=True).start()
        except RuntimeError:
            _revision_poll_lock.release()
    return _revision_snapshot


class _FolderCache:
    def __init__(self):
        self.lock = threading.RLock()
        self.mode = None
        self.mode_signature = None
        self.prestige_files = {}
        self.prestige_signature = None
        self.prestige = None
        self.prestige_times = []
        self.notifications = {}
        self.bad_blocks = 0
        self.events = []
        self.signature = None
        self.complete = False
        self.saved = None


class _IndexState:
    def __init__(self, root):
        self.root = root
        self.timezone = _timezone_key()
        self.root_complete = False
        self.root_time = None
        self.folders = {}
        self.active = {}
        self.sealed = {}
        self.newest = None
        self.revision_folder = None
        self.sealed_events = []
        self.sealed_prestige = None
        self.dirty = False
        self.save_needed = False
        self.pending = set()
        self.pending_lock = threading.Lock()
        self.stop = threading.Event()
        self.worker = None


def _folder_cache_for(folder):
    folder = os.path.normpath(os.fspath(folder))
    with _log_cache_lock:
        # 경로 변경 전의 감시 호출이 늦게 끝나도 이전 캐시를 다시 등록하지 않는다.
        if _cache_root is not None and os.path.dirname(folder) != _cache_root:
            return _FolderCache()
        return _folder_cache.setdefault(folder, _FolderCache())


def _scan_folder_files(folder):
    backend, notifications = [], []
    with os.scandir(folder) as entries:
        for entry in entries:
            name = entry.name.lower()
            if not name.endswith(".log"):
                continue
            if ("backend_" not in name and "push-notifications" not in name) or not entry.is_file():
                continue
            stat = entry.stat()
            # Windows의 DirEntry.stat()은 inode가 0일 수 있어 별도로 식별자를 얻는다.
            item = (entry.path, (stat.st_mtime_ns, stat.st_size, stat.st_ctime_ns, entry.inode()))
            if "backend_" in name:
                backend.append(item)
            if "push-notifications" in name:
                notifications.append(item)
    return backend, notifications


def _folder_files(folder):
    # 지도 감시도 같은 문자열 경로를 사용한다. 종료 여부 판단은 색인에서만 한다.
    return _scan_folder_files(os.fspath(folder))


def _file_signature(files):
    return tuple(sorted(files, key=lambda item: os.path.normcase(item[0])))


def _disk_signature(folder_time, files):
    # 상대 파일 이름만 저장하며 알림 순서도 보존한다.
    return [folder_time, [[[os.path.basename(path), *sig] for path, sig in group] for group in files]]


def _read_index(root):
    try:
        with (paths.CACHE_DIR / "log_index.json").open(encoding="utf-8") as stream:
            data = json.load(stream)
        if (type(data) is not dict or type(data.get("v")) is not int or data.get("v") != 1 or data.get("root") != root
                or data.get("timezone") != _timezone_key() or type(data.get("folders")) is not dict):
            return {}
        return data["folders"]
    except (OSError, ValueError, TypeError, RecursionError):
        return {}


def _timezone_key():
    # 알림 시각은 로컬 시각이므로 시간대가 달라지면 다시 해석한다.
    return [list(time.tzname), time.timezone, time.altzone, time.daylight,
            time.localtime().tm_isdst, os.environ.get("TZ")]


def _restore_folder(cache, saved, signature, backend_signature):
    try:
        if type(saved) is not dict or saved["signature"] != signature:
            return False
        timeline = saved["mode"]
        times = [datetime.fromisoformat(value) for value in timeline[0]]
        modes = timeline[1]
        if (len(times) != len(modes) or times != sorted(times)
                or any(type(mode) is not bool for mode in modes)
                or any(when.tzinfo is not None for when in times)):
            return False
        prestige = [datetime.fromisoformat(value) for value in saved["prestige"]]
        if any(when.tzinfo is not None for when in prestige):
            return False
        events = []
        for row in saved["events"]:
            message_id, quest_id, kind, stamp, trader, mode = row
            when = datetime.fromisoformat(stamp)
            if (message_id is not None and not isinstance(message_id, str)
                    or not isinstance(quest_id, str) or type(kind) is not int or kind not in STATUS_NAMES
                    or trader is not None and not isinstance(trader, str)
                    or type(mode) is not bool or when.tzinfo is not None):
                return False
            events.append((message_id, {"quest_id": quest_id, "type": kind, "time": when,
                                        "trader_id": trader}, mode))
        bad_blocks = saved.get("bad_blocks", 0)
        if type(bad_blocks) is not int or bad_blocks < 0:
            return False
        cache.mode = {"tail": None, "last": times[-1] if times else None, "timeline": (times, modes)}
        cache.mode_signature = backend_signature
        cache.prestige_signature = backend_signature
        cache.prestige = max(prestige, default=None)
        cache.prestige_times = prestige
        cache.bad_blocks = bad_blocks
        cache.events = events
        cache.signature = signature
        cache.complete = True
        cache.saved = _sealed_data(cache)
        return True
    except (KeyError, ValueError, TypeError, IndexError, OverflowError):
        return False


def _save_index(state):
    # 같은 PC의 두 실행도 서로 임시 파일을 덮어쓰지 않는다. 실패하면 메모리만 사용한다.
    temporary = None
    try:
        paths.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data = {"v": 1, "root": state.root, "timezone": state.timezone,
                "folders": {os.path.basename(folder): cache.saved for folder, cache in state.sealed.items()}}
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=paths.CACHE_DIR,
                                         prefix="log_index-", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, paths.CACHE_DIR / "log_index.json")
    except (OSError, ValueError):
        pass
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _rebuild_aggregate(state):
    # 폴더 열거 순서와 파일 내 순서를 유지해야 중복 _id와 같은 시각의 결과도 같다.
    state.sealed_events = [((state.folders[folder], position), message_id, event)
                           for folder, cache in state.sealed.items()
                           for position, (message_id, event, mode) in enumerate(cache.events) if mode]
    state.sealed_events.sort(key=lambda item: item[0])
    state.sealed_prestige = max((cache.prestige for cache in state.sealed.values()
                                 if cache.prestige is not None), default=None)
    state.dirty = False


def _revalidate_sealed(state):
    # 디스크 순회는 전역 잠금 밖에서 한다. 요청은 바뀐 폴더 이름만 받아 재구성한다.
    with state.pending_lock:
        snapshot = [(folder, cache, cache.signature) for folder, cache in state.sealed.items()]
    changed = []
    for folder, cache, signature in snapshot:
        if state.stop.is_set():
            return
        try:
            current = _disk_signature(os.stat(folder).st_mtime_ns, _scan_folder_files(folder))
        except FileNotFoundError:
            current = None
        except OSError:
            # 이전 결과는 보존하되 다음 조회를 불완전으로 표시하고 다시 읽는다.
            changed.append((folder, cache))
            continue
        if current != signature:
            changed.append((folder, cache))
    with state.pending_lock:
        state.pending.update(folder for folder, cache in changed if state.sealed.get(folder) is cache)


def _index_worker(state):
    while not state.stop.wait(REVALIDATE_SECONDS):
        _revalidate_sealed(state)


def _log_folders():
    """루트 수정 시각이 같으면 활성 폴더만 반환한다. 호출자는 전역 잠금을 잡는다."""
    global _cache_root, _index_state
    root = os.path.normpath(os.fspath(settings.logs_dir()))
    if (_index_state is None or root != _cache_root
            or _index_state.timezone != _timezone_key()):
        if _index_state is not None:
            _index_state.stop.set()
        _folder_cache.clear()
        _cache_root = root
        _index_state = _IndexState(root)
    state = _index_state
    try:
        root_time = os.stat(root).st_mtime_ns
    except OSError:
        state.root_complete = False
        return state
    if state.root_time != root_time or state.worker is None or not state.root_complete:
        folders, newest, newest_time = {}, None, None
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    if entry.name.lower().startswith("log_") and entry.is_dir():
                        folders[entry.path] = len(folders)
                        created = entry.stat().st_ctime_ns
                        if newest_time is None or created > newest_time:
                            newest, newest_time = entry.path, created
        except FileNotFoundError:
            folders, newest = {}, None
        except OSError:
            # 일시적인 읽기 실패는 이미 확인한 결과를 버리지 않고 다음 호출에 재시도한다.
            state.root_complete = False
            return state
        state.root_complete = True
        state.save_needed = state.worker is not None
        state.root_time = root_time
        state.folders = folders
        state.newest = newest
        with state.pending_lock:
            for folder in list(_folder_cache):
                if folder not in folders:
                    _folder_cache.pop(folder, None)
                    state.active.pop(folder, None)
                    state.sealed.pop(folder, None)
            for folder in folders:
                if folder not in state.sealed:
                    state.active[folder] = _folder_cache_for(folder)
            if newest in state.sealed:
                state.active[newest] = state.sealed.pop(newest)
        state.dirty = True
        if state.worker is None:
            saved = _read_index(root)
            state.save_needed = not saved
            # 두 번째 시작에도 파일 크기·mtime을 확인하여 폴더 mtime이 안 바뀐 덮어쓰기를 잡는다.
            # 이 확인도 읽는 중 안내에 개수가 나오게 진행 숫자를 올린다 (전에는 0 / 0 으로 멈춰 보였음)
            # '읽는 중' 표시는 이 실제 확인 단계에서만 켠다. 상태 확인(log_rev)이 2초마다 부르는
            # 짧은 목록 확인까지 켜면 안내 화면이 닫히지 않는다 (2026-11-21 시험: 2분 넘게 0/0)
            with _reading():
                to_check = list(state.active.items())
                with _reading_lock:
                    _reading_progress.update(done=0, total=len(to_check))
                for folder, cache in to_check:
                    _advance_log_reading()
                    entry = saved.get(os.path.basename(folder))
                    if entry is None or folder == newest:
                        continue
                    try:
                        files = _scan_folder_files(folder)
                        signature = _disk_signature(os.stat(folder).st_mtime_ns, files)
                        old_enough = time.time_ns() - max((sig[0] for group in files for _, sig in group), default=0) > SEALED_AFTER_NS
                        if old_enough and _restore_folder(cache, entry, signature, _file_signature(files[0])):
                            state.sealed[folder] = state.active.pop(folder)
                    except OSError:
                        continue
            state.save_needed = state.save_needed or set(saved) != {os.path.basename(folder) for folder in state.sealed}
            state.worker = threading.Thread(target=_index_worker, args=(state,), daemon=True)
            state.worker.start()
    with state.pending_lock:
        for folder in state.pending:
            if folder in state.sealed:
                # 저장본에는 tail 위치가 없으므로 변경된 종료 폴더는 처음부터 읽는다.
                previous = state.sealed[folder]
                cache = _FolderCache()
                cache.events, cache.signature = previous.events, previous.signature
                cache.prestige, cache.prestige_times = previous.prestige, previous.prestige_times
                _folder_cache[folder] = state.active[folder] = cache
                state.sealed.pop(folder)
                state.dirty = True
                state.save_needed = True
        state.pending.clear()
    return state


def line_time(line: str) -> datetime | None:
    match = _LOG_TIME.match(line)
    try:
        return datetime.fromisoformat(match[1]) if match else None
    except ValueError:
        return None


def mode_timeline(folder: Path, *, _signature=None, _cache=None) -> tuple:
    """추가 구간만 읽고 모드가 바뀐 시각만 보관한다. 원문 로그는 보관하지 않는다."""
    from app.map_watch import LogTail

    def requests(lines):
        events = []
        for line in lines:
            if "Request HTTPS" not in line:
                continue
            request = _MODE_REQUEST.search(line)
            when = line_time(line)
            # 모드 고르기 요청은 항상 PvP 주소로 감 → 판단에서 뺌 (로그에는 끝에 '.' 이 붙어 기록됨: /client/game/mode.)
            if not request or when is None or request[2].rstrip("/.") == "/client/game/mode":
                continue
            events.append((when, request[1].startswith("gw-pve")))
        return sorted(events, key=lambda event: event[0])

    folder_cache = _cache if _cache is not None else _folder_cache_for(folder)
    with folder_cache.lock:
        if _signature is None:
            try:
                _signature = _file_signature(_folder_files(folder)[0])
            except OSError:
                _signature = ()
        if folder_cache.mode is not None and folder_cache.mode_signature == _signature:
            return folder_cache.mode["timeline"]
        if folder_cache.mode is None or folder_cache.mode["tail"] is None:
            folder_cache.mode = {"tail": LogTail(Path(folder), backend_only=True), "last": None, "timeline": ([], [])}
        cache = folder_cache.mode
        tail = cache["tail"]
        events = requests(tail.new_lines())
        rebuild = bool(tail.reset_paths) or bool(events and cache["last"] and events[0][0] < cache["last"])
        if rebuild:
            # 교체·축소·과거 시각의 추가만 해당 폴더를 재구성한다.
            tail = cache["tail"] = LogTail(Path(folder), backend_only=True)
            events = requests(tail.new_lines())
            cache["timeline"] = ([], [])
            cache["last"] = None
        tail.lines_by_file.clear()
        if events:
            times, modes = (list(part) for part in cache["timeline"])
            for when, mode in events:
                if not modes or modes[-1] != mode:
                    times.append(when)
                    modes.append(mode)
            cache["last"] = events[-1][0]
            cache["timeline"] = (times, modes)
        # 읽기에 실패한 파일은 서명을 확정하지 않아 다음 호출에서 다시 시도한다.
        folder_cache.mode_signature = (_signature if not tail.read_errors and all(
            tail.positions.get(Path(path)) == signature[1] for path, signature in _signature) else None)
        return cache["timeline"]


def is_pve(timeline: tuple, when: datetime | None) -> bool:
    if when is None:
        return True
    times, modes = timeline
    index = bisect_right(times, when) - 1
    return index < 0 or modes[index]



def _read_prestige_tail(path, previous, signature):
    """완성된 마지막 줄 다음부터 읽는다. 교체/축소/같은 크기 덮어쓰기는 재구성."""
    with open(path, "rb") as stream:
        stat = os.fstat(stream.fileno())
        identity = (stat.st_dev, stat.st_ino, getattr(stat, "st_birthtime_ns", None))
        reset = (previous is None or previous["identity"] != identity
                 or stat.st_size < previous["size"]
                 or (stat.st_size == previous["size"] and stat.st_mtime_ns != previous["mtime"]))
        offset = 0 if reset else previous["offset"]
        stream.seek(offset)
        chunk = stream.read()
    end = chunk.rfind(b"\n") + 1
    lines = chunk[:end].decode("utf-8", errors="replace").splitlines()
    times = [] if reset else list(previous["times"])
    times.extend(when for line in lines if PRESTIGE_RE.match(line) and (when := line_time(line)))
    return {"signature": signature, "identity": identity, "offset": offset + end,
            "size": stat.st_size, "mtime": stat.st_mtime_ns, "times": times}, lines


def _prime_backend(cache, backend, signature):
    """첫 읽기는 backend 한 번으로 모드와 프레스티지를 함께 추출한다."""
    requests = []
    complete = True
    from app.map_watch import LogTail
    tail = LogTail(Path(backend[0][0]).parent, backend_only=True) if backend else None
    for path, file_signature in _file_signature(backend):
        try:
            parsed, lines = _read_prestige_tail(path, None, file_signature)
        except OSError:
            complete = False
            continue
        cache.prestige_files[path] = parsed
        file_path = Path(path)
        tail.positions[file_path] = parsed["offset"]
        tail.identities[file_path] = parsed["identity"]
        tail.file_stats[file_path] = (parsed["size"], parsed["mtime"])
        for line in lines:
            if "Request HTTPS" not in line:
                continue
            when = line_time(line)
            request = _MODE_REQUEST.search(line)
            if when is not None and request and request[2].rstrip("/.") != "/client/game/mode":
                requests.append((line[:23], when, request[1].startswith("gw-pve")))
    # LogTail의 문자열 시각 정렬 뒤 datetime 정렬과 동률 순서까지 맞춘다.
    requests.sort(key=lambda item: item[0])
    requests.sort(key=lambda item: item[1])
    times, modes = [], []
    for _, when, mode in requests:
        if not modes or modes[-1] != mode:
            times.append(when)
            modes.append(mode)
    cache.mode = {"tail": tail, "last": requests[-1][1] if requests else None, "timeline": (times, modes)}
    cache.mode_signature = signature if complete else None


def _refresh_folder(folder, cache, files, signature):
    backend, notifications = files
    backend_signature = _file_signature(backend)
    if cache.signature == signature and cache.complete:
        return
    old_prestige, old_times = cache.prestige, cache.prestige_times
    if cache.mode is None:
        _prime_backend(cache, backend, backend_signature)
    timeline = (cache.mode["timeline"] if cache.mode_signature == backend_signature
                else mode_timeline(folder, _signature=backend_signature, _cache=cache))
    complete = cache.mode_signature == backend_signature
    if cache.prestige_signature != backend_signature:
        present = {path for path, _ in backend}
        for path in list(cache.prestige_files):
            if path not in present:
                del cache.prestige_files[path]
        times = []
        for path, file_signature in backend:
            cached = cache.prestige_files.get(path)
            if cached is None or cached["signature"] != file_signature:
                try:
                    parsed, _ = _read_prestige_tail(path, cached, file_signature)
                except OSError:
                    complete = False
                    continue
                cache.prestige_files[path] = cached = parsed
            times.extend(when for when in cached["times"] if is_pve(timeline, when))
        cache.prestige = max(times, default=None)
        cache.prestige_times = times
        cache.prestige_signature = backend_signature if complete else None
    if not complete:
        # backend에서 확정하지 못했을 때만 이전 프레스티지를 보존한다.
        cache.prestige, cache.prestige_times = old_prestige, old_times
        cache.prestige_signature = None
    present = {path for path, _ in notifications}
    for path in list(cache.notifications):
        if path not in present:
            del cache.notifications[path]
    events = []
    for path, file_signature in notifications:
        try:
            parsed, last = _read_notifications(path, cache.notifications, file_signature)
        except OSError:
            complete = False
            continue
        for batch in (parsed, (last,) if last is not None else ()):
            for message_id, event in batch:
                events.append((message_id, event, is_pve(timeline, event["time"])))
    if not complete:
        # 읽기 실패는 삭제가 아니다. 마지막으로 확인한 결과를 유지하고 다시 시도한다.
        cache.complete = False
        return
    cache.bad_blocks = sum(parsed["bad_blocks"] + parsed["last_bad"]
                           for parsed in cache.notifications.values())
    cache.events = events
    cache.signature = signature
    cache.complete = complete


def _sealed_data(cache):
    return {
        "signature": cache.signature,
        "bad_blocks": cache.bad_blocks,
        "prestige": [when.isoformat() for when in cache.prestige_times],
        "events": [[message_id, event["quest_id"], event["type"], event["time"].isoformat(),
                    event["trader_id"], mode] for message_id, event, mode in cache.events],
        "mode": [[when.isoformat() for when in cache.mode["timeline"][0]], cache.mode["timeline"][1]],
    }


@_reading()
def _refresh_index():
    state = _log_folders()
    with _reading_lock:
        _reading_progress.update(done=0, total=len(state.active))
    for folder, cache in list(state.active.items()):
        with cache.lock:
            try:
                folder_time = os.stat(folder).st_mtime_ns
                files = _scan_folder_files(folder)
                signature = _disk_signature(folder_time, files)
                _refresh_folder(folder, cache, files, signature)
                last_write = max((sig[0] for group in files for _, sig in group), default=0)
                # 가장 최근 생성 폴더 제외 + 1시간 무기록 + 읽기 전후 폴더 시각 동일.
                if (cache.complete and folder != state.newest
                        and time.time_ns() - last_write > SEALED_AFTER_NS
                        and os.stat(folder).st_mtime_ns == folder_time):
                    cache.saved = _sealed_data(cache)
                    cache.notifications.clear()
                    cache.prestige_files.clear()
                    cache.mode["tail"] = None
                    with state.pending_lock:
                        state.sealed[folder] = state.active.pop(folder)
                    state.dirty = True
                    state.save_needed = True
            except OSError:
                # 잠긴 파일은 저장·봉인하지 않고 다음 요청에서 다시 확인한다.
                cache.complete = False
                continue
            finally:
                _advance_log_reading()
    if state.dirty:
        _rebuild_aggregate(state)
    if state.save_needed:
        _save_index(state)
        state.save_needed = False
    return state


def prestige_status() -> tuple[datetime | None, bool]:
    """프레스티지와 같은 조회의 완전성을 함께 반환한다. 불완전한 결과는 초기화에 쓰지 않는다."""
    with _log_cache_lock:
        state = _refresh_index()
        found = [cache.prestige for cache in state.active.values() if cache.prestige is not None]
        if state.sealed_prestige is not None:
            found.append(state.sealed_prestige)
        complete = state.root_complete and all(cache.complete for cache in state.active.values())
        return max(found, default=None), complete


def last_prestige() -> datetime | None:
    """알림 읽기가 실패해도 backend에서 확인한 프레스티지는 보존한다."""
    return prestige_status()[0]

STATUS_NAMES = {10: "진행 중", 11: "실패", 12: "완료"}

# 일일·주간 반복 과제의 틀 번호 (여러 상인이 같은 번호로 여러 번 줌) → 목록에서 뺀다
# 2026-11-17 로그로 확인: 여러 상인·여러 날짜에 같은 번호로 완료 기록, 보상은 루블 + 문서 아이템
REPEATABLE_TEMPLATES = {
    "616041eb031af660100c9967",
    "61604635c725987e815b1a46",
    "616051e63f96cc089c1cf37f",
    "616052ea3054fc0e2c24ce6e",  # 같은 계열 번호 (처치 과제로 알려짐, 로그에선 아직 못 봄)
}

def load_manual_quests() -> dict:
    """공통 보완 파일의 퀘스트 항목 (메타데이터 제외)."""
    return corrections.section("quests")


def quest_name(quest_id: str, tasks: dict, tr, manual: dict) -> str:
    """퀘스트 이름: 게임 직접 확인 → dev(same_as 포함) → SPT → 위키·블로그 → id."""
    m = manual.get(quest_id, {})
    lang = i18n.current_lang()
    name = m.get("name" if lang == "ko" else "name_en")
    if m.get("from") == "game" and name:
        return name
    task = tasks.get(quest_id) or tasks.get(m.get("same_as"))
    if task:
        return tr(task["name"])
    return (tarkov_api.spt_quest_name(quest_id, lang)
            or name or m.get("name")
            or i18n.t("server.unknownQuest", id=quest_id))

def quest_name_en(quest_id: str, tasks: dict, tr, manual: dict) -> str | None:
    """퀘스트의 영어 이름 (한국어 화면에서 제목 옆에 작게 보여 줌)"""
    m = manual.get(quest_id, {})
    if m.get("from") == "game" and m.get("name_en"):
        return m["name_en"]
    task = tasks.get(quest_id) or tasks.get(m.get("same_as"))
    return ((tr.en.get(task["name"]) if task else None)
            or tarkov_api.spt_quest_name(quest_id, "en")
            or (m.get("name_en") if not task else None))


# 로그 한 줄의 시작: "2026-11-08 13:32:44.478|..."
LINE_START = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


def _quest_event(note):
    if not isinstance(note, dict) or not isinstance(note.get("message"), dict):
        raise ValueError("notification-shape")
    message = note["message"]
    kind = message.get("type")
    if type(kind) is not int:
        raise ValueError("notification-type")
    if kind not in STATUS_NAMES:
        return None
    stamp = message.get("dt")
    template = message.get("templateId")
    # Unix 시각 1970~3000년: PC 현재 날짜와 무관하게 손상된 값만 배제한다.
    if (type(stamp) not in (int, float) or not 0 <= stamp < 32503680000
            or not math.isfinite(stamp) or not isinstance(template, str)
            or message.get("_id") is not None and not isinstance(message["_id"], str)
            or note.get("dialogId") is not None and not isinstance(note["dialogId"], str)):
        raise ValueError("notification-fields")
    try:
        when = datetime.fromtimestamp(stamp)
    except (ValueError, OverflowError, OSError) as error:
        raise ValueError("notification-time") from error
    parts = template.split(" ")
    # 기존과 같은 일반 퀘스트만 보관한다. 모드·프레스티지 필터는 조회할 때 적용한다.
    if len(parts) != 2 or parts[1] not in {"description", "successMessageText", "failMessageText"}:
        return None
    if parts[0] in REPEATABLE_TEMPLATES:
        return None
    return (message.get("_id"), {
        "quest_id": parts[0],
        "type": kind,
        "time": when,
        "trader_id": note.get("dialogId"),
    })


def _read_notifications(log_file: str | Path, files: dict, signature):
    """확정한 알림은 작은 이벤트로 보관하고, 마지막 블록은 시작 위치만 기억한다."""
    cached = files.get(log_file)
    if cached is not None and cached["signature"] == signature:
        return cached["events"], cached["last"]
    with open(log_file, "rb") as fh:
        stat = os.fstat(fh.fileno())
        identity = (stat.st_dev, stat.st_ino, getattr(stat, "st_birthtime_ns", None))
        # 같은 크기의 덮어쓰기도 초기화한다. 축소는 미완성 블록 앞의 offset과도 별개다.
        if (cached is None or cached["identity"] != identity or stat.st_size < cached["size"]
                or (stat.st_size == cached["size"] and stat.st_mtime_ns != cached["mtime"])):
            cached = {"events": [], "offset": 0, "bad_blocks": 0}
        fh.seek(cached["offset"])
        block, block_start, last = [], None, None
        new_events = []
        offset = cached["offset"]

        def parsed_block(final=False):
            try:
                return _quest_event(json.loads("\n".join(block)))
            except (ValueError, RecursionError):
                # 마지막 블록은 다음 추가 읽기에서 다시 검사하므로 중복 집계하지 않는다.
                if final:
                    cached["last_bad"] = 1
                else:
                    cached["bad_blocks"] += 1
                return None

        position = offset
        for raw in fh.read().splitlines(keepends=True):
            line_start = position
            position += len(raw)
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if LINE_START.match(line):
                if block_start is not None:
                    event = parsed_block()
                    if event is not None:
                        new_events.append(event)
                    block, block_start = [], None
            if block_start is None:
                if "Got notification | ChatMessageReceived" in line:
                    block_start = line_start
                # 쓰는 도중 잘린 헤더·UTF-8 문자는 다음에 같은 바이트부터 다시 읽는다.
                offset = position if raw.endswith(b"\n") else line_start
            else:
                # 알림 헤더 자체는 JSON에 넣지 않는다.
                if line_start != block_start:
                    block.append(line)
                offset = block_start
        cached["last_bad"] = 0
        if block_start is not None:
            last = parsed_block(final=True)
            offset = block_start
        cached["events"].extend(new_events)
        cached.update(identity=identity, size=max(stat.st_size, position), mtime=stat.st_mtime_ns,
                      offset=offset, last=last, signature=signature)
        files[log_file] = cached
    return cached["events"], cached["last"]


def log_rev() -> str:
    """맵 감시와 같은 활성 폴더의 알림 크기. 전체 색인 잠금을 기다리지 않는다."""
    from app.map_watch import latest_log_folder
    try:
        # 쉬는 폴더의 순회 확인은 watcher가 맡는다. 평소 API는 활성 폴더만 조회한다.
        newest = latest_log_folder(scan_history=False)
        if newest is None:
            return ""
        size = sum(sig[1] for _, sig in _scan_folder_files(newest)[1])
        return f"{os.path.basename(newest)}:{size}"
    except OSError:
        return _revision_snapshot  # 파일 잠금은 기록 삭제/새 revision으로 취급하지 않는다.


def read_quest_events(since: datetime | None = None) -> list[dict]:
    """봉인 집계와 활성 폴더를 기존 순서로 합치고 필터 뒤에 중복 제거한다."""
    if since is None:
        since = last_prestige() or datetime.min
    with _log_cache_lock:
        state = _refresh_index()
        active = [((state.folders[folder], position), message_id, event)
                  for folder, cache in state.active.items()
                  for position, (message_id, event, mode) in enumerate(cache.events) if mode]
        active.sort(key=lambda item: item[0])
        events = {}
        for _, message_id, event in heapq.merge(state.sealed_events, active, key=lambda item: item[0]):
            if event["time"] >= since:
                events[message_id] = dict(event)
        return sorted(events.values(), key=lambda event: event["time"])


def current_quest_status(since: datetime | None = None) -> dict[str, dict]:
    """퀘스트 id → {상태, 마지막 기록 시각}. 마지막 기록이 이긴다."""
    return status_from_events(read_quest_events(since))


def missing_starts(events: list[dict]) -> set[str]:
    """'시작' 기록 없이 완료·실패만 있는 퀘스트 = 로그 앞부분이 지워졌다는 단서.
    온전한 로그에서는 0개 (2026-11-17 확인). 어느 PC에서나 로그만 보고 같은 기준으로 판단."""
    started = {e["quest_id"] for e in events if e["type"] == 10}
    return {e["quest_id"] for e in events if e["type"] in (11, 12)} - started


def status_from_events(events: list[dict]) -> dict[str, dict]:
    """시간순 기록 → 퀘스트 id → {상태, 마지막 시각, 마지막 비어 있지 않은 상인 id}"""
    status = {}
    for event in events:
        status[event["quest_id"]] = {
            "status": STATUS_NAMES[event["type"]],
            "time": event["time"],
            "trader_id": (event.get("trader_id")
                          or status.get(event["quest_id"], {}).get("trader_id")),
        }
    return status


if __name__ == "__main__":
    from app import tarkov_api

    print(tarkov_api.update_all())
    tasks, tr = tarkov_api.get_tasks()
    manual = load_manual_quests()

    status = current_quest_status()
    counts = {}
    for info in status.values():
        counts[info["status"]] = counts.get(info["status"], 0) + 1

    print(f"기준: {last_prestige() or "(프레스티지 기록 없음 → 전체 로그)"} 이후 기록")
    print("요약:", ", ".join(f"{k} {v}개" for k, v in counts.items()))
    print()
    print("[진행 중인 퀘스트]")
    for quest_id, info in sorted(status.items(), key=lambda x: x[1]["time"]):
        if info["status"] == "진행 중":
            name = quest_name(quest_id, tasks, tr, manual)
            print(f"  {name}  (받은 시각 {info['time']:%m-%d %H:%M})")
    print("\n* = tarkov.dev에 없어서 data/corrections.json에 직접 적은 이름")

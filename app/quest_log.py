"""게임 로그(push-notifications)를 읽어서 퀘스트 상태를 알아내는 파일.

로그에 남는 퀘스트 알림 (new_message의 type)
- 10 = 퀘스트 시작
- 11 = 퀘스트 실패
- 12 = 퀘스트 완료

같은 퀘스트에 여러 기록이 있으면 '가장 마지막 기록'이 지금 상태다.
(예: 실패(11) 후 다시 시작(10)하면 → 진행 중)
"""

import json
import os
import re
import threading
from bisect import bisect_right
from datetime import datetime
from pathlib import Path

from app import corrections, i18n, settings, tarkov_api


# ---------- 가장 최근 프레스티지(초기화) 찾기 ----------
# 규칙 (어느 PC에서나 같음):
#   backend 로그에서 PvE 서버(gw-pve)로 보낸 "/client/prestige/obtain" 요청 중 가장 최근 시각.
#   찾지 못하면 None → 남아 있는 로그 전체를 처음부터 읽는다.
# 로그 파일이 많아서, 이미 읽은 파일은 기억해 두고 새로 생기거나 바뀐 파일만 다시 읽는다.
PRESTIGE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*Request HTTPS.*https://gw-pv[ep][^/]*/client/prestige/obtain")
_folder_cache = {}
_log_cache_lock = threading.RLock()
_cache_root = None
_prestige_answer = (None, None)
_LOG_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)")
_MODE_REQUEST = re.compile(r"https://(gw-pv[ep][^/.:]*)\.escapefromtarkov\.com(?::\d+)?(/client/[^\s|?]*)")


class _FolderCache:
    def __init__(self):
        self.lock = threading.RLock()
        self.mode = None
        self.mode_signature = None
        self.prestige_files = {}
        self.prestige_signature = None
        self.prestige = None
        self.notifications = {}


def _folder_cache_for(folder):
    with _log_cache_lock:
        # 경로 변경 전의 감시 호출이 늦게 끝나도 이전 캐시를 다시 등록하지 않는다.
        if _cache_root is not None and folder.parent != _cache_root:
            return _FolderCache()
        if folder not in _folder_cache:
            _folder_cache[folder] = _FolderCache()
        return _folder_cache[folder]


# 게임은 가장 최근에 만든 로그 폴더 하나에만 쓴다. 나머지(끝난 세션) 폴더는 폴더 자체가
# 그대로면 파일 목록·정보를 다시 확인하지 않는다 (폴더 552개 × 파일마다 정보 조회가 느렸음).
_newest_folder = None
_files_memo = {}  # 폴더 → (폴더 수정 시각, _folder_files 결과)


def _log_folders():
    """폴더 목록은 한 번 열거하고, 사라진 폴더와 이전 설치 경로의 캐시를 버린다."""
    global _cache_root, _prestige_answer, _newest_folder
    root = settings.logs_dir()
    newest, newest_time = None, None
    try:
        with os.scandir(root) as entries:
            folders = []
            for entry in entries:
                if entry.name.lower().startswith("log_") and entry.is_dir():
                    folder = Path(entry.path)
                    folders.append(folder)
                    created = entry.stat().st_ctime_ns
                    if newest_time is None or created > newest_time:
                        newest, newest_time = folder, created
    except OSError:
        folders = []
    with _log_cache_lock:
        _newest_folder = newest
        if root != _cache_root:
            _folder_cache.clear()
            _files_memo.clear()
            _prestige_answer = (None, None)
            _cache_root = root
        present = set(folders)
        for folder in list(_folder_cache):
            if folder not in present:
                del _folder_cache[folder]
                _prestige_answer = (None, None)
        for folder in list(_files_memo):
            if folder not in present:
                del _files_memo[folder]
        # 이 호출은 같은 캐시 객체를 끝까지 사용한다. 삭제·경로 변경 뒤 재등록하지 않는다.
        return root, [(folder, _folder_cache_for(folder)) for folder in folders]


def _folder_files(folder):
    """원문 대신 디렉터리 항목의 수정 시각·크기·식별 정보만 확인한다."""
    sealed = folder != _newest_folder
    folder_time = None
    if sealed:
        try:
            folder_time = os.stat(folder).st_mtime_ns
        except OSError:
            folder_time = None
        memo = _files_memo.get(folder)
        if memo and folder_time is not None and memo[0] == folder_time:
            return memo[1]
    result = _scan_folder_files(folder)
    if sealed and folder_time is not None:
        with _log_cache_lock:
            _files_memo[folder] = (folder_time, result)
    return result


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
            # Windows의 DirEntry.stat()은 inode가 0일 수 있어 별도로 파일 식별자를 얻는다.
            item = (Path(entry.path), (stat.st_mtime_ns, stat.st_size, stat.st_ctime_ns, entry.inode()))
            if "backend_" in name:
                backend.append(item)
            if "push-notifications" in name:
                notifications.append(item)
    return backend, notifications


def _file_signature(files):
    return tuple(sorted(files))


def line_time(line: str) -> datetime | None:
    match = _LOG_TIME.match(line)
    return datetime.fromisoformat(match[1]) if match else None


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
        if folder_cache.mode is None:
            folder_cache.mode = {"tail": LogTail(folder, backend_only=True), "last": None, "timeline": ([], [])}
        cache = folder_cache.mode
        tail = cache["tail"]
        events = requests(tail.new_lines())
        rebuild = bool(tail.reset_paths) or bool(events and cache["last"] and events[0][0] < cache["last"])
        if rebuild:
            # 교체·축소·과거 시각의 추가만 해당 폴더를 재구성한다.
            tail = cache["tail"] = LogTail(folder, backend_only=True)
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
        folder_cache.mode_signature = (_signature if all(
            tail.positions.get(path) == signature[1] for path, signature in _signature) else None)
        return cache["timeline"]


def is_pve(timeline: tuple, when: datetime | None) -> bool:
    if when is None:
        return True
    times, modes = timeline
    index = bisect_right(times, when) - 1
    return index < 0 or modes[index]



def last_prestige() -> datetime | None:
    """폴더별 변경분만 읽고, 모든 폴더의 서명이 같으면 최종 날짜도 재사용한다."""
    global _prestige_answer
    root, folders = _log_folders()
    signatures, found = [], []
    for folder, cache in folders:
        # 디스크 작업은 해당 폴더만 잠근다. 다른 폴더의 감시를 막지 않는다.
        with cache.lock:
            try:
                backend, _ = _folder_files(folder)
            except OSError:
                continue
            signature = _file_signature(backend)
            if cache.prestige_signature != signature:
                present = {path for path, _ in backend}
                for path in list(cache.prestige_files):
                    if path not in present:
                        del cache.prestige_files[path]
                timeline = mode_timeline(folder, _signature=signature, _cache=cache)
                times, complete = [], cache.mode_signature == signature
                for path, file_signature in backend:
                    cached = cache.prestige_files.get(path)
                    if cached is None or cached[0] != file_signature:
                        try:
                            with path.open(encoding="utf-8", errors="replace") as fh:
                                parsed = [when for line in fh
                                          if PRESTIGE_RE.match(line) and (when := line_time(line))]
                        except OSError:
                            complete = False
                            continue
                        cache.prestige_files[path] = cached = (file_signature, parsed)
                    times.extend(when for when in cached[1] if is_pve(timeline, when))
                cache.prestige = max(times, default=None)
                cache.prestige_signature = signature if complete else None
            signatures.append((folder, cache.prestige_signature, cache.prestige))
            if cache.prestige is not None:
                found.append(cache.prestige)
    key = (root, tuple(signatures))
    with _log_cache_lock:
        if _cache_root == root:
            if _prestige_answer[0] != key:
                _prestige_answer = (key, max(found, default=None))
            return _prestige_answer[1]
    return max(found, default=None)

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
    message = note.get("message", {})
    kind = message.get("type")
    if kind not in STATUS_NAMES:
        return None
    parts = message.get("templateId", "").split(" ")
    # 기존과 같은 일반 퀘스트만 보관한다. 모드·프레스티지 필터는 조회할 때 적용한다.
    if len(parts) != 2 or parts[1] not in {"description", "successMessageText", "failMessageText"}:
        return None
    if parts[0] in REPEATABLE_TEMPLATES:
        return None
    return (message.get("_id"), {
        "quest_id": parts[0],
        "type": kind,
        "time": datetime.fromtimestamp(message.get("dt", 0)),
        "trader_id": note.get("dialogId"),
    })


def _read_notifications(log_file: Path, files: dict, signature):
    """확정한 알림은 작은 이벤트로 보관하고, 마지막 블록은 시작 위치만 기억한다."""
    cached = files.get(log_file)
    if cached is not None and cached["signature"] == signature:
        return cached["events"], cached["last"]
    with log_file.open("rb") as fh:
        stat = os.fstat(fh.fileno())
        identity = (stat.st_dev, stat.st_ino, getattr(stat, "st_birthtime_ns", None))
        # 같은 크기의 덮어쓰기도 초기화한다. 축소는 미완성 블록 앞의 offset과도 별개다.
        if (cached is None or cached["identity"] != identity or stat.st_size < cached["size"]
                or (stat.st_size == cached["size"] and stat.st_mtime_ns != cached["mtime"])):
            cached = {"events": [], "offset": 0}
        fh.seek(cached["offset"])
        block, block_start, last = [], None, None
        new_events = []
        offset = cached["offset"]

        def parsed_block():
            try:
                return _quest_event(json.loads("\n".join(block)))
            except json.JSONDecodeError:
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
        if block_start is not None:
            last = parsed_block()
            offset = block_start
        cached["events"].extend(new_events)
        cached.update(identity=identity, size=max(stat.st_size, position), mtime=stat.st_mtime_ns,
                      offset=offset, last=last, signature=signature)
        files[log_file] = cached
    return cached["events"], cached["last"]


def log_rev() -> str:
    """퀘스트 기록이 바뀌었는지 알려주는 짧은 표시 (가장 최근 로그 폴더의 알림 로그 크기).
    화면이 2초마다 물어보고, 값이 바뀌면 퀘스트를 새로 불러옴. 파일 크기만 보므로 아주 가벼움"""
    try:
        folders = sorted(settings.logs_dir().glob("log_*"))  # 폴더 이름이 날짜순 (log_2026.11.18_23-13-16_…)
        if not folders:
            return ""
        newest = folders[-1]
        size = sum(f.stat().st_size for f in newest.glob("*push-notifications*.log"))
        return f"{newest.name}:{size}"
    except OSError:
        return ""


def read_quest_events(since: datetime | None = None) -> list[dict]:
    """since(기본: 가장 최근 프레스티지) 이후의 퀘스트 기록을 시간 순서대로 돌려준다.
    프레스티지 기록이 없으면 남아 있는 로그 전체를 읽는다."""
    if since is None:
        since = last_prestige() or datetime.min
    events = {}
    _, folders = _log_folders()
    for folder, cache in folders:
        with cache.lock:
            try:
                backend, notifications = _folder_files(folder)
            except OSError:
                continue
            present = {path for path, _ in notifications}
            for path in list(cache.notifications):
                if path not in present:
                    del cache.notifications[path]
            signature = _file_signature(backend)
            # 프레스티지 확인에서 이미 갱신한 시간표라면 tail/glob도 다시 호출하지 않는다.
            timeline = (cache.mode["timeline"] if cache.mode is not None and cache.mode_signature == signature
                        else mode_timeline(folder, _signature=signature, _cache=cache))
            for log_file, file_signature in notifications:
                try:
                    parsed, last = _read_notifications(log_file, cache.notifications, file_signature)
                except OSError:
                    continue
                for batch in (parsed, (last,) if last is not None else ()):
                    for message_id, event in batch:
                        if event["time"] >= since and is_pve(timeline, event["time"]):
                            # 필터 뒤에 중복 제거해야 과거·PvP 중복이 기존 PvE 알림을 지우지 않는다.
                            events[message_id] = dict(event)
    return sorted(events.values(), key=lambda e: e["time"])


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

"""게임 로그를 지켜보면서 지금 어느 맵에 있는지 알아내는 파일.

로그에서 보는 줄 (2026-11-16 확인)
- application 로그 `scene preset path:maps/customs_preset.bundle ...` → 맵 로딩 시작 (어느 맵인지 여기서 앎)
- application 로그 `GameStarted`                                  → 레이드 시작
- backend 로그   `/client/match/local/end` 요청                    → 레이드 끝 (로비로 돌아옴)

맵 이름은 tarkov.dev 지도 정보의 scenePath(예: maps/customs_preset.bundle)와
로그의 경로가 똑같아서 그대로 연결한다. → 새 맵이 나와도 따로 고칠 필요 없음
"""

import re
import os
import json
import time
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app import i18n, settings, tarkov_api, quest_log


SCENE_RE = re.compile(r"scene preset path:(maps/\S+?\.bundle)")
TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

LOBBY, LOADING, IN_RAID = "lobby", "loading", "raid"  # 게임 상태 코드 (화면 글자는 사전 topbar.state*)


@dataclass
class MapState:
    status: str = LOBBY
    map_id: str | None = None  # tarkov.dev 지도 id
    map_name: str | None = None  # 한국어 지도 이름
    since: datetime | None = None  # 이 상태가 시작된 시각
    cancelled: bool = False  # 매칭을 취소해서 로비로 온 것 (레이드를 하지 않았음)

    def __str__(self) -> str:
        when = f" ({self.since:%m-%d %H:%M:%S}부터)" if self.since else ""  # i18n-ok: 개발용 콘솔
        if self.status == LOBBY:
            return f"[{self.status}]{when}"
        return f"[{self.status}] {self.map_name}{when}"


class MapLookup:
    """로그의 scenePath → (tarkov.dev 지도 id, 한국어 이름)"""

    def __init__(self):
        tarkov_api.update_all()
        maps = tarkov_api.load("maps")["maps"]
        tr = tarkov_api.Translator("maps")
        self.by_scene = {
            m["scenePath"]: (m["id"], tr(m["name"]))
            for m in maps.values() if m.get("scenePath")
        }

    def find(self, scene_path: str) -> tuple[str | None, str]:
        return self.by_scene.get(scene_path, (None, i18n.t("server.unknownMap", path=scene_path)))


_folder_lock = threading.Lock()
_folder_scan = {"root": None, "root_time": None, "files": {}, "active": None,
                "checked": 0.0, "cursor": 0, "folders": []}
_bad_date_reported = False


def _activity(folder):
    """내용 대신 파일 정보만 읽는다. 빈 폴더는 파일이 있는 폴더보다 낮은 우선순위."""
    files = []
    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.name.lower().endswith(".log") and entry.is_file():
                stat = entry.stat()
                files.append((entry.name, stat.st_mtime_ns, stat.st_size, entry.inode()))
    return tuple(sorted(files))


def latest_log_folder(*, scan_history: bool = True) -> Path | None:
    """파일 쓰기로 선택. 목록은 재사용하고 쉬는 폴더는 매번 64개씩만 확인한다."""
    root = settings.logs_dir()
    now = time.monotonic()
    with _folder_lock:
        cache = _folder_scan
        if cache["root"] != root:
            cache.update(root=root, root_time=None, files={}, active=None,
                         checked=0.0, cursor=0, folders=[])
        if now - cache["checked"] < 0.9:
            return cache["active"]
        cache["checked"] = now
        try:
            root_time = root.stat().st_mtime_ns
            if cache["root_time"] != root_time:
                with os.scandir(root) as entries:
                    folders = [Path(e.path) for e in entries if e.name.lower().startswith("log_") and e.is_dir()]
                cache["folders"] = folders
                cache["root_time"] = root_time
                present = set(folders)
                cache["files"] = {f: value for f, value in cache["files"].items() if f in present}
                # 초기만 전체 검사. 새 폴더는 이름/생성 시각과 무관하게 즉시 후보로 검사.
                candidates = [f for f in folders if f not in cache["files"]]
            else:
                candidates = []
            folders = cache["folders"]
            start = cache["cursor"]
            if scan_history:
                candidates.extend(folders[start:start + 64])
                cache["cursor"] = start + 64 if start + 64 < len(folders) else 0
            if cache["active"] in folders:
                candidates.append(cache["active"])
            changed, observed_writes = [], []
            for folder in dict.fromkeys(candidates):
                try:
                    activity = _activity(folder)
                except OSError:
                    continue  # 잠금/일시 오류에는 마지막 선택과 정보를 보존한다.
                previous = cache["files"].get(folder)
                cache["files"][folder] = activity
                if activity and activity != previous:
                    changed.append(folder)
                    if previous is not None:
                        observed_writes.append(folder)
            def newest_file(folder):
                return max((row[1] for row in cache["files"].get(folder, ())), default=0)
            # 이미 쓰던 파일의 실제 변화는 과거 시각을 보존한 새 복사본보다 우선한다.
            active = cache["active"]
            # 새 세션은 현재 시계와 가까운 쓰기로 구별한다(5분은 탐색/파일 잠금 여유).
            fresh = [f for f in changed if abs(time.time_ns() - newest_file(f)) < 300 * 10**9]
            if observed_writes or fresh:
                # 시계를 뒤로 맞춘 뒤의 실제 쓰기도 옛 미래 mtime보다 우선한다.
                cache["active"] = max(set(observed_writes + fresh), key=newest_file)
            elif changed:
                # 새 후보가 과거 복사본이면 현재 파일의 최신 쓰기를 유지한다.
                best = max(changed, key=newest_file)
                if active not in folders or newest_file(best) >= newest_file(active):
                    cache["active"] = best
            if cache["active"] not in folders:
                cache["active"] = max(cache["files"], key=newest_file, default=None)
                if cache["active"] is None and folders:
                    cache["active"] = folders[0]  # 비어 있거나 잠긴 파일도 다음 검사에서 다시 시도
        except OSError:
            pass
        return cache["active"]


def apply_line(state: MapState, line: str, lookup: MapLookup) -> bool:
    """로그 한 줄을 보고 상태를 바꾼다. 바뀌었으면 True."""
    time_match = TIME_RE.match(line)
    if not time_match:
        return False
    global _bad_date_reported
    try:
        when = datetime.strptime(time_match[1], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        if not _bad_date_reported:
            logging.getLogger(__name__).warning("Invalid game-log date skipped")  # i18n-ok: 개발 로그, 원문 제외
            _bad_date_reported = True
        return False

    scene = SCENE_RE.search(line)
    if scene:
        map_id, map_name = lookup.find(scene[1])
        # 매칭을 다시 잡으면 같은 줄이 또 나옴 → 같은 맵 로딩 중이면 무시
        if state.status == LOADING and state.map_id == map_id:
            return False
        state.status, state.map_id, state.map_name, state.since = LOADING, map_id, map_name, when
        state.cancelled = False
        return True

    if "|application|GameStarted:" in line and state.status == LOADING:
        state.status, state.since = IN_RAID, when
        return True

    if "Request HTTPS" in line and "/client/match/local/end" in line and state.status != LOBBY:
        state.status, state.map_id, state.map_name, state.since = LOBBY, None, None, when
        state.cancelled = False
        return True

    # 매칭 중 취소 → 바로 로비 (2026-11-18 인게임 확인: 이 줄이 없으면 '맵 로딩 중'에 멈춰 있었음)
    if "Local game matching cancelled" in line and state.status == LOADING:
        state.status, state.map_id, state.map_name, state.since = LOBBY, None, None, when
        state.cancelled = True
        return True

    return False


class LogTail:
    """로그 파일에 새로 추가된 줄만 읽어 온다 (tail 처럼)."""

    def __init__(self, folder: Path, backend_only: bool = False):
        self.folder = folder
        self.backend_only = backend_only
        self.identities = {}
        self.file_stats = {}
        self.reset_paths = set()
        self.read_errors = set()
        self.lines_by_file = {}
        self.positions: dict[Path, int] = {}
        self.leftover: dict[Path, str] = {}

    def _files(self) -> list[Path]:
        return ([] if self.backend_only else sorted(self.folder.glob("*application_*.log"))) + sorted(self.folder.glob("*backend_*.log"))

    def new_lines(self) -> list[str]:
        lines = []
        files = self._files()
        self.read_errors = set()
        self.reset_paths = set(self.positions) - set(files)
        self.lines_by_file = {}
        for path in self.reset_paths:
            self.positions.pop(path, None)
            self.leftover.pop(path, None)
            self.identities.pop(path, None)
            self.file_stats.pop(path, None)
        for path in files:
            start = self.positions.get(path, 0)
            try:
                with path.open("rb") as fh:
                    stat = os.fstat(fh.fileno())
                    identity = (stat.st_dev, stat.st_ino, getattr(stat, "st_birthtime_ns", None))
                    previous = self.file_stats.get(path)
                    if (stat.st_size < start or self.identities.get(path, identity) != identity
                            or previous is not None and previous[0] == stat.st_size
                            and previous[1] != stat.st_mtime_ns):
                        start = 0
                        self.leftover.pop(path, None)
                        self.reset_paths.add(path)
                    self.identities[path] = identity
                    fh.seek(start)
                    chunk = fh.read()
                    self.positions[path] = fh.tell()
                    self.file_stats[path] = (self.positions[path], stat.st_mtime_ns)
            except OSError:
                self.read_errors.add(path)
                continue  # 게임이 파일을 쓰는 중이면 다음에 다시
            text = self.leftover.pop(path, "") + chunk.decode("utf-8", errors="replace")
            parts = text.split("\n")
            if parts and not text.endswith("\n"):
                self.leftover[path] = parts.pop()  # 아직 덜 써진 마지막 줄은 다음에
            self.lines_by_file[path] = [p.rstrip("\r") for p in parts if p]
            lines.extend(self.lines_by_file[path])
        # application, backend 두 파일의 줄을 시간 순서로 섞기
        lines.sort(key=lambda l: l[:23])
        if not self.backend_only:
            timeline = quest_log.mode_timeline(self.folder)
            lines = [line for line in lines if quest_log.is_pve(timeline, quest_log.line_time(line))]
        return lines


# ---------- 로그 형식 점검 (2026-11-23 사용자) ----------
# 게임 업데이트로 로그 모양이 바뀌면 자동 전환이 조용히 멈춘다 → 알아챌 수 있게 표시.
# - "unreadable": 이 폴더에서 로그 줄이 HEALTH_MIN_LINES 넘게 쌓였는데 '날짜 시간|' 모양 줄이나
#   서버 요청 줄(Request HTTPS)이 하나도 없음. 로비에 오래 있어도 서버 요청 줄은 계속 쌓이므로 헷갈리지 않음
# - "no_map": 레이드 시작 요청(/client/match/local/start)은 보이는데 그 전에 와야 할 맵 로딩 줄이 없음
#   (맵 로딩 줄이 약 10초 먼저 나옴, 2026-11-23 로그 확인)
# 판단은 로그 파일만 보고 함 (게임 메모리·파일 수정 없음). 새 폴더(게임 재시작)면 처음부터 다시 셈
HEALTH_MIN_LINES = 200
_health_lock = threading.Lock()
_health = {"lines": 0, "stamped": 0, "requests": 0, "no_map": False}


def _reset_health() -> None:
    with _health_lock:
        _health.update(lines=0, stamped=0, requests=0, no_map=False)


def _count_health(tail: "LogTail") -> None:
    """PvE 거르기 전의 원래 줄로 센다 (형식이 바뀌면 거르기에서 다 빠지므로)"""
    lines = [line for rows in tail.lines_by_file.values() for line in rows]
    if not lines:
        return
    with _health_lock:
        _health["lines"] += len(lines)
        _health["stamped"] += sum(1 for line in lines if TIME_RE.match(line) and "|" in line[:40])
        _health["requests"] += sum(1 for line in lines if "Request HTTPS" in line)


def _check_raid_start(state: MapState, line: str) -> None:
    if "Request HTTPS" in line and "/client/match/local/start" in line:
        with _health_lock:
            _health["no_map"] = state.status == LOBBY  # 맵 로딩 줄을 못 알아봤음
    elif state.status != LOBBY:
        with _health_lock:
            _health["no_map"] = False


def log_problem() -> str | None:
    """화면 경고용: None(정상) / "unreadable" / "no_map" """
    with _health_lock:
        if _health["lines"] >= HEALTH_MIN_LINES and (_health["stamped"] == 0 or _health["requests"] == 0):
            return "unreadable"
        return "no_map" if _health["no_map"] else None


# ---------- 퀘스트 기록(push-notifications) 점검 (LOG-03, 2026-11-23 사용자) ----------
# 퀘스트 시작·완료·실패는 이 파일의 'Got notification | ChatMessageReceived' 줄 + 다음 줄들의 JSON 으로 남는다.
# 게임 업데이트로 모양이 바뀌면 퀘스트 반영이 조용히 멈추므로 알린다.
# - 머리말 바뀜: 파일이 NOTIF_MIN_BYTES 넘게 쌓이고 JSON 덩어리('{' 줄)도 있는데 'Got notification |' 머리말이 0개
#   (채팅 머리말만 보면 안 됨: 채팅 없이 거래소 판매·그룹 알림만 있는 세션이 있음, 이 PC 로그 5폴더)
# - 내용 바뀜: 채팅 머리말이 NOTIF_MIN_BLOCKS 개 이상인데 JSON 으로 읽히는 것이 0개
# 기준값 이유 (2026-11-23 이 PC 로그 556폴더 확인): 알림 하나가 약 0.8KB 라 4KB ≈ 알림 5개.
# 알림이 없는 세션은 연결 줄 하나(약 120바이트)뿐이라 걸리지 않음
NOTIF_MIN_BYTES = 4096
NOTIF_MIN_BLOCKS = 3
NOTIF_MAX_BYTES = 16 * 1024 * 1024  # 이보다 크면 읽지 않음 (점검 때문에 느려지지 않게)
_ANY_HEADER = "Got notification |"
_CHAT_HEADER = _ANY_HEADER + " ChatMessageReceived"
_notif_lock = threading.Lock()
_notif = {"folder": None, "sizes": None, "problem": False}


def notification_health(folder: Path) -> dict:
    """폴더 하나의 push-notifications 를 세어 돌려준다 (시험 도구도 씀).
    덩어리 경계는 실제 퀘스트 파서(quest_log)와 같게: 날짜 줄이 나오거나 파일 끝에서만 닫는다.
    못 읽은 파일이 있으면 locked=True (지난 판단을 유지하고 다시 시도하게)"""
    stats = {"bytes": 0, "json_starts": 0, "any_headers": 0, "headers": 0, "readable": 0, "locked": False}
    for path in sorted(folder.glob("*push-notifications*.log")):
        try:
            raw = path.read_bytes()
        except OSError:
            stats["locked"] = True
            continue
        stats["bytes"] += len(raw)
        block = None

        def close(block):
            try:
                if isinstance(json.loads("\n".join(block)), dict):
                    stats["readable"] += 1
            except (ValueError, RecursionError):
                pass

        for line in raw.decode("utf-8", errors="replace").splitlines():
            if block is not None and quest_log.LINE_START.match(line):
                close(block)
                block = None
            if line.strip() == "{":
                stats["json_starts"] += 1
            if _ANY_HEADER in line:
                stats["any_headers"] += 1
            if _CHAT_HEADER in line:
                stats["headers"] += 1
            if block is None:
                if _CHAT_HEADER in line:
                    block = []
            else:
                block.append(line)  # 날짜 모양이 바뀌면 뒤 머리말까지 합쳐져 못 읽음 (실제 파서와 같음)
        if block is not None:
            close(block)
    return stats


def notification_problem_of(stats: dict) -> bool:
    if stats["any_headers"] == 0:
        return stats["bytes"] >= NOTIF_MIN_BYTES and stats["json_starts"] > 0
    return stats["headers"] >= NOTIF_MIN_BLOCKS and stats["readable"] == 0


def _check_notifications(folder: Path | None) -> None:
    """감시 스레드에서 1초마다. 파일 이름·크기·수정 시각·파일 번호가 바뀌었을 때만 다시 센다"""
    sig = None
    if folder:
        try:
            sig = tuple(sorted((p.name, st.st_size, st.st_mtime_ns, st.st_ino)
                               for p in folder.glob("*push-notifications*.log") for st in [p.stat()]))
        except OSError:
            return  # 잠깐 못 읽으면 지난 판단 유지
    with _notif_lock:
        if _notif["folder"] == folder and _notif["sizes"] == sig:
            return
        problem = _notif["problem"] if _notif["folder"] == folder else False
    if not sig:
        problem = False
    elif sum(item[1] for item in sig) <= NOTIF_MAX_BYTES:
        stats = notification_health(folder)
        if stats["locked"]:
            with _notif_lock:
                _notif.update(folder=folder, problem=problem)  # sizes 는 그대로 → 다음 회차에 다시 시도
            return
        problem = notification_problem_of(stats)
    else:
        problem = False  # 너무 크면 검사하지 않음
    with _notif_lock:
        _notif.update(folder=folder, sizes=sig, problem=problem)


def notification_problem() -> bool:
    with _notif_lock:
        return _notif["problem"]


# 게임 버전: 로그 폴더 이름 끝 (예: log_2026.11.22_16-24-50_1.3.0.2.48779 → 1.3.0.2.48779). 경로는 밖으로 내보내지 않음
_VERSION_RE = re.compile(r"_(\d+(?:\.\d+){2,})$")


def game_version() -> str | None:
    with _notif_lock:
        folder = _notif["folder"]
    match = _VERSION_RE.search(folder.name) if folder else None
    return match.group(1) if match else None


def _feed(tail: "LogTail", state: MapState, lookup: MapLookup, on_change=None) -> None:
    lines = tail.new_lines()
    _count_health(tail)
    for line in lines:
        changed = apply_line(state, line, lookup)
        _check_raid_start(state, line)
        if changed and on_change:
            on_change(state)


def watch(interval: float = 1.0, on_change=print) -> None:
    """로그를 계속 지켜보다가 상태가 바뀌면 on_change(상태)를 부른다."""
    lookup = MapLookup()
    state = MapState()
    folder = latest_log_folder()
    tail = LogTail(folder) if folder else None
    _reset_health()
    _check_notifications(folder)

    # 처음에는 지금 폴더의 기존 줄을 다 읽어서 현재 상태를 맞춘다
    if tail:
        _feed(tail, state, lookup)
    on_change(state)

    while True:
        time.sleep(interval)
        newest = latest_log_folder()
        if newest != folder:  # 새 폴더나 설치 위치 변경은 빈 상태부터 다시 읽는다.
            folder, tail, state = newest, LogTail(newest) if newest else None, MapState()
            _reset_health()
            on_change(state)
        _check_notifications(folder)
        if not tail:
            continue
        _feed(tail, state, lookup, on_change)


def replay(folder: Path) -> list[str]:
    """폴더 하나의 로그를 처음부터 읽으면서 상태 변화를 기록해 돌려준다 (테스트용)."""
    lookup = MapLookup()
    state = MapState()
    history = []
    for line in LogTail(folder).new_lines():
        if apply_line(state, line, lookup):
            history.append(str(state))
    return history


if __name__ == "__main__":
    import sys

    if "--watch" in sys.argv:
        print("로그를 지켜보는 중... (끝내려면 Ctrl+C)")
        try:
            watch(on_change=lambda s: print(time.strftime("%H:%M:%S"), s))
        except KeyboardInterrupt:
            print("끝!")
    else:
        # 폴더 이름을 주면 그 폴더, 없으면 가장 최근 폴더를 다시 읽어 본다
        args = [a for a in sys.argv[1:] if not a.startswith("--")]
        folder = settings.logs_dir() / args[0] if args else latest_log_folder()
        print(f"로그 폴더: {folder.name}\n")
        for entry in replay(folder):
            print(" ", entry)

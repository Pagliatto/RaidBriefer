"""지금 게임 상태(로비 / 맵 로딩 중 / 레이드 중)를 뒤에서 계속 지켜보는 파일.

서버가 켜질 때 start() 를 한 번 부르면, 따로 돌아가는 일꾼(스레드)이
app/map_watch.py 로 게임 로그를 1초마다 확인하고 최신 상태를 기억해 둔다.
화면은 /api/state 로 그 값을 물어본다.
"""

import os
import subprocess
import threading
import time

from app import map_watch, report_log, tarkov_api

GAME_OFF = "off"  # 게임 꺼짐 (화면 글자는 사전)
GAME_EXE = "EscapeFromTarkov.exe"

_lock = threading.Lock()
_state = {"status": map_watch.LOBBY, "map_id": None, "map_name": None, "since": None, "raid_minutes": None, "cancelled": False, "pooled": False}
_game_running = False
_raid_minutes = {}  # 지도 id → 레이드 제한 시간(분), tarkov.dev 맵 정보
_started = False

# 지난 판 상태 거르기 (2026-11-26 사용자: 게임을 새로 켜자마자 '레이드 중'으로 보임).
# 레이드 중에 게임이 꺼지면 그 로그 폴더는 '레이드 끝' 줄 없이 끝나서 감시 상태가 계속 '레이드 중'으로 남는다.
# 게임이 꺼져 있는 동안은 '게임 꺼짐'에 가려지지만, 다시 켜면 새 로그 폴더가 생길 때까지(이 PC 는 11초)
# 지난 판 맵이 그대로 보였다. 레이드·로딩은 게임이 꺼진 순간을 넘어 이어질 수 없으므로,
# '게임 꺼짐을 확인한 때보다 먼저 알게 된 상태'는 지난 판 것으로 보고 로비로 보여 준다.
# 시각은 PC 시계가 아니라 켜진 뒤 흐른 시간(monotonic)으로 잰다 (시계를 바꿔도 같게).
# 단, 로그 폴더를 '처음 읽어서' 안 상태는 알게 된 때가 늦을 수 있다 (프로그램을 막 켰는데 첫 읽기가 느리고
# 그사이 게임을 켠 경우) → 그 상태만 로그에 적힌 시작 시각(PC 시계)이 꺼짐 확인보다 앞인지도 본다.
# 실시간으로 본 줄에는 PC 시계를 쓰지 않는다 (시계를 뒤로 돌려도 진행 중인 레이드를 버리지 않게).
OFF_CHECKS = 2  # 연속으로 이만큼 '꺼짐'이어야 꺼진 것으로 (확인 한 번이 잠깐 틀려도 진행 중인 레이드를 버리지 않게)
_state_seen = 0.0  # 감시가 지금 상태를 알려 준 때
_state_initial = False  # 지금 상태가 폴더를 처음 읽어서 안 것인지 (감시를 시작한 뒤 첫 알림)
_first_report = False  # 감시를 (다시) 시작해서 첫 알림을 기다리는 중
_off_seen = None  # 게임 꺼짐을 마지막으로 확인한 때 (확인한 적 없으면 None)
_off_wall = None  # 같은 때의 PC 시계 (time.time)
_off_count = 0


def _map_label(map_id: str | None) -> str | None:
    """신고용 기록의 맵 이름: tarkov.dev 영어 이름 (언어와 무관하게 같게)"""
    try:
        info = tarkov_api.load("maps")["maps"].get(map_id) if map_id else None
        return info.get("normalizedName") or info.get("name") if info else map_id
    except Exception:
        return map_id


def _on_change(s: map_watch.MapState) -> None:
    global _state_seen, _state_initial, _first_report
    with _lock:
        was_raid = _state["status"] == map_watch.IN_RAID
        _state.update(
            status=s.status,
            map_id=s.map_id,
            map_name=s.map_name,
            # 화면(자바스크립트)이 쓰기 쉬운 밀리초 시각. 로그 시각은 이 PC의 시간대 기준
            since=int(s.since.timestamp() * 1000) if s.since else None,
            raid_minutes=_raid_minutes.get(s.map_id),
            cancelled=s.cancelled,
            pooled=s.pooled,
        )
        _state_seen = time.monotonic()
        _state_initial, _first_report = _first_report, False
    # 신고용 기록 (흐름): 레이드에 들어감/나옴만
    if s.status == map_watch.IN_RAID and not was_raid:
        report_log.add("map_in", map=_map_label(s.map_id))
    elif was_raid and s.status != map_watch.IN_RAID:
        report_log.add("map_out")


def _run() -> None:
    global _first_report
    delay = 1
    while True:
        try:
            tarkov_api.update_all()
            for m in tarkov_api.load("maps")["maps"].values():
                _raid_minutes[m["id"]] = m.get("raidDuration")
            delay = 1
            tarkov_api.profile_event("game_log_watch_start")
            with _lock:
                _first_report = True  # watch 는 폴더의 기존 줄을 다 읽은 뒤 상태를 한 번 알려 준다
            map_watch.watch(interval=1.0, on_change=_on_change)
        except Exception as error:
            print(f"게임 상태 확인을 다시 시도해요: {error}")  # i18n-ok: 개발용 콘솔
        time.sleep(delay)
        delay = min(delay * 2, 60)


def _tasklist() -> str:
    """시스템 도구는 전체 경로로 (같은 이름의 다른 프로그램·PATH 문제를 피함). SystemRoot 가 없으면 이름만"""
    root = os.environ.get("SystemRoot")
    return os.path.join(root, "System32", "tasklist.exe") if root else "tasklist"


def is_game_running() -> bool | None:
    """작업 관리자처럼 '실행 중인 프로그램 이름'만 확인 (게임 메모리·파일은 건드리지 않음)
    True/False = 확인함, None = 확인 실패 (못 켬·시간 초과·오류 코드) → 부르는 쪽이 직전 결과를 유지"""
    try:
        result = subprocess.run(
            [_tasklist(), "/FI", f"IMAGENAME eq {GAME_EXE}", "/NH", "/FO", "CSV"],
            capture_output=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # 검은 창이 번쩍이지 않게
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:  # 못 찾음은 0 으로 끝남 (안내 글만 나옴) → 0 이 아니면 확인 실패
        return None
    # 한국어 윈도우는 cp949 글자로 답함. 찾는 이름은 영어라 깨진 글자는 무시
    return GAME_EXE.lower() in result.stdout.decode("ascii", errors="ignore").lower()


def _note_process(running: bool | None) -> None:
    """게임 켜짐 확인 결과 하나를 반영한다 (None = 확인 실패 → 직전 결과 유지)"""
    global _game_running, _off_seen, _off_wall, _off_count
    # 확인 실패는 '게임 꺼짐'이 아님 → 직전 결과 유지 (처음부터 실패면 직전 값이 꺼짐이라 그대로 꺼짐)
    if running is None:
        return
    with _lock:
        changed = running != _game_running
        _game_running = running
        _off_count = 0 if running else _off_count + 1
        if _off_count >= OFF_CHECKS:
            _off_seen, _off_wall = time.monotonic(), time.time()
    if changed:
        report_log.add("game_on" if running else "game_off")


def _watch_process() -> None:
    while True:
        _note_process(is_game_running())
        time.sleep(3)


def start() -> None:
    """서버 시작 때 한 번만 부르면 됨"""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_run, name="game-state", daemon=True).start()
    threading.Thread(target=_watch_process, name="game-process", daemon=True).start()


def _map_name(map_id: str | None, fallback: str | None) -> str | None:
    """지도 id → 지금 고른 언어의 맵 이름 (언어를 바꾸면 바로 반영)"""
    if not map_id:
        return fallback
    try:
        maps = tarkov_api.load("maps")["maps"]
        info = maps.get(map_id)
        return tarkov_api.Translator("maps")(info["name"]) if info else fallback
    except Exception:
        return fallback


def get() -> dict:
    with _lock:
        state = dict(_state)
        # 게임 꺼짐을 확인하기 전에 알게 된 레이드·로딩 = 지난 판 것 → 로비로 (맨 위 설명)
        stale = _off_seen is not None and (_state_seen <= _off_seen or (
            _state_initial and state["since"] is not None and state["since"] <= _off_wall * 1000))
    if stale and state["status"] != map_watch.LOBBY:
        state.update(status=map_watch.LOBBY, map_id=None, map_name=None, since=None, raid_minutes=None, cancelled=False, pooled=False)
    state["map_name"] = _map_name(state.get("map_id"), state.get("map_name"))
    # 로그 형식 경고는 게임이 켜져 있을 때만 (map_watch.log_problem)
    state["log_problem"] = map_watch.log_problem() if _game_running else None
    if not _game_running:  # 게임이 꺼져 있으면 로그 상태와 상관없이 '게임 꺼짐'
        state.update(status=GAME_OFF, map_id=None, map_name=None, since=None, raid_minutes=None, cancelled=False, pooled=False)
    return state

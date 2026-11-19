"""RaidBriefer 프로그램 창 (pywebview).

브라우저 대신 프로그램 창 하나로 띄운다. 창을 닫으면 서버·로그 감시까지 모두 꺼진다.
- 화면 그리기는 윈도우에 들어 있는 WebView2(엣지와 같은 엔진)가 함
- 창 크기·위치는 user_data/window.json 에 기억 (처음엔 960×1080 = FHD 모니터 반쪽)
- 지도 표시 설정 같은 브라우저 저장소는 user_data/webview 에 남김 (기본값은 닫을 때 지워짐)
- 이미 켜져 있으면 서버를 또 켜지 않고 켜져 있는 서버에 창만 붙임

실행: python -m app.window   (보통은 '프로그램_실행.bat')
"""

import json
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

import webview

from app import paths, server, updater

USER_DIR = paths.USER_DIR
WINDOW_FILE = USER_DIR / "window.json"
ICON_FILE = Path(__file__).resolve().parent / "static" / "icons" / "RaidBriefer.ico"

DEFAULT_SIZE = (960, 1080)  # FHD(1920×1080) 모니터의 반쪽, 세로로 긴 창
MIN_SIZE = (480, 600)


def _url(port: int) -> str:
    return f"http://{server.HOST}:{port}"


def _is_our_server(port: int) -> bool:
    """그 번호에 RaidBriefer 서버가 이미 켜져 있는지 (/api/state 가 대답하는지)"""
    try:
        with urllib.request.urlopen(_url(port) + "/api/state", timeout=1) as res:
            return "status" in json.loads(res.read().decode("utf-8"))
    except Exception:
        return False


def _start_server(port: int) -> None:
    """서버를 뒤에서 켜고, 대답할 때까지 잠깐 기다림"""
    threading.Thread(target=server.serve, args=(port,), name="server", daemon=True).start()
    for _ in range(100):  # 최대 10초
        if _is_our_server(port):
            return
        time.sleep(0.1)


def _load_geometry() -> dict:
    try:
        saved = json.loads(WINDOW_FILE.read_text(encoding="utf-8"))
        w, h = int(saved["width"]), int(saved["height"])
        if w < MIN_SIZE[0] or h < MIN_SIZE[1]:
            raise ValueError
        geo = {"width": w, "height": h}
        # 위치는 모니터를 뺐을 때 화면 밖으로 나갈 수 있어서, 아주 이상한 값이면 버림
        x, y = saved.get("x"), saved.get("y")
        if isinstance(x, int) and isinstance(y, int) and -10000 < x < 20000 and -1000 < y < 10000:
            geo.update(x=x, y=y)
        return geo
    except (OSError, ValueError, KeyError, TypeError):
        return {"width": DEFAULT_SIZE[0], "height": DEFAULT_SIZE[1]}


def _save_geometry(window) -> None:
    try:
        geo = {"x": window.x, "y": window.y, "width": window.width, "height": window.height}
        if geo["width"] >= MIN_SIZE[0] and geo["height"] >= MIN_SIZE[1]:  # 최소화 상태 값은 저장 안 함
            USER_DIR.mkdir(exist_ok=True)
            WINDOW_FILE.write_text(json.dumps(geo), encoding="utf-8")
    except Exception:
        pass  # 창 위치를 못 남겨도 프로그램 사용에는 문제없음


def main() -> None:
    updater.cleanup()  # 업데이트 직후라면 작업 폴더 정리
    port = server.PORT
    if "--port" in sys.argv:  # 시험용: 이미 켜 둔 프로그램과 겹치지 않게 다른 번호로 (예: --port 8790)
        port = int(sys.argv[sys.argv.index("--port") + 1])
    if not _is_our_server(port):
        _start_server(port)
    # 이미 켜져 있으면(브라우저 방식으로 켰거나 창을 하나 더 열었을 때) 그 서버를 같이 씀

    window = webview.create_window(
        "RaidBriefer",
        _url(port),
        **_load_geometry(),
        min_size=MIN_SIZE,
        background_color="#0b1316",  # 화면이 뜨기 전 하얗게 번쩍이지 않게 (앱 배경색)
        text_select=True,  # 퀘스트 이름 복사 등 브라우저처럼 글자 선택 가능
    )
    window.events.closing += lambda: _save_geometry(window)
    updater.set_exit_handler(window.destroy)  # 업데이트할 때 창을 닫아서 도우미가 파일을 바꿀 수 있게

    USER_DIR.mkdir(exist_ok=True)
    webview.start(
        private_mode=False,  # 지도 표시 설정·마지막 맵 기억이 창을 닫아도 남게
        storage_path=str(USER_DIR / "webview"),
        icon=str(ICON_FILE) if ICON_FILE.exists() else None,
    )
    # 창을 닫으면 여기로 옴 → 서버·로그 감시는 daemon 이라 프로그램과 함께 끝남


def run() -> None:
    """프로그램 시작점 (python -m app.window, exe 모두 여기로)"""
    try:
        main()
    except Exception:
        # 검은 창 없이(pythonw·exe) 켜면 오류가 안 보이므로 파일로 남김
        USER_DIR.mkdir(exist_ok=True)
        (USER_DIR / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise
    sys.exit(0)


if __name__ == "__main__":
    run()

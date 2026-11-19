"""exe 로 묶을 때의 시작 파일 (tools/build_exe.py 가 사용). 직접 실행해도 프로그램 창이 뜸."""

import os
import sys
from pathlib import Path


def _unblock_own_files() -> None:
    """인터넷에서 받은 zip 을 풀면 윈도우가 파일마다 '인터넷에서 받음' 표시(Zone.Identifier)를 붙임.
    프로그램 창 부품(.NET)은 이 표시가 있는 DLL 을 거부해서 창이 안 뜸
    → exe 와 같이 들어 있는 **우리 프로그램 파일**에서만 그 표시를 지움 (오른쪽 클릭 → 속성 → '차단 해제' 와 같음)"""
    if not getattr(sys, "frozen", False):
        return
    bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    for path in bundle.rglob("*.dll"):
        try:
            os.remove(f"{path}:Zone.Identifier")
        except OSError:
            pass  # 표시가 없으면(대부분) 그냥 넘어감


if __name__ == "__main__":
    _unblock_own_files()  # 창 부품을 불러오기 전에 해야 함
    from app.window import run

    run()

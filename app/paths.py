"""파일 위치 한곳에 모음.

- 그냥 실행(python -m app.window): 모든 파일이 프로젝트 폴더 기준
- exe 로 실행(PyInstaller): 프로그램에 묶인 읽기 전용 파일(data, 화면)은 묶음 안,
  사용자 기록(user_data)·받은 데이터(cache)는 **exe 옆 폴더**에 저장
  → 압축을 푼 폴더째로 옮기면 기록도 같이 옮겨짐
"""

import sys
from pathlib import Path

if getattr(sys, "frozen", False):  # exe 로 실행 중
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    HOME_DIR = Path(sys.executable).resolve().parent
else:
    BUNDLE_DIR = HOME_DIR = Path(__file__).resolve().parent.parent

USER_DIR = HOME_DIR / "user_data"   # 진행 기록·설정·창 위치 (사람마다 다름, 배포에 넣지 않음)
CACHE_DIR = HOME_DIR / "cache"      # tarkov.dev 에서 받은 데이터
DATA_DIR = BUNDLE_DIR / "data"      # 프로그램과 같이 나가는 데이터 (스토리 등)

"""남은 파일 정리 (켤 때 한 번).

- 저장하다 프로그램이 꺼지면 남는 반쯤 쓴 임시 파일(*.tmp): 1시간 넘은 것만 지움
- 손상 파일 백업(*.broken*.json): 파일마다 최근 3개만 남김
우리 폴더(cache·user_data)의 바로 아래와 정해 둔 하위 폴더만 본다. 게임 폴더·webview 는 건드리지 않음.
"""

import time
from pathlib import Path

from app import paths

TEMP_AGE_SECONDS = 3600
KEEP_BROKEN = 3


def _temp_folders() -> list[tuple[Path, ...]]:
    """(맨 위 폴더, 하위 폴더 …) 순서. 그 경로의 모든 폴더가 링크가 아니어야 정리한다."""
    return [(paths.CACHE_DIR,), (paths.CACHE_DIR, paths.CACHE_DIR / "map_svg"),
            (paths.USER_DIR,), (paths.USER_DIR, paths.USER_DIR / "gamma")]


def _real_folder(folder: Path) -> bool:
    """링크(심볼릭 링크·정션)로 다른 곳을 가리키지 않는 진짜 폴더인지"""
    try:
        return folder.is_dir() and not folder.is_symlink() and not folder.is_junction()
    except OSError:
        return False


def _plain_files(chain: tuple[Path, ...], pattern: str) -> list[Path]:
    """링크가 아닌 일반 파일만. 폴더 경로 중 하나라도 링크·정션이면 다른 곳의 파일일 수 있어 빈 목록
    (없는 폴더·읽을 수 없는 폴더도 빈 목록)"""
    if not all(_real_folder(folder) for folder in chain):
        return []
    try:
        return [p for p in chain[-1].glob(pattern) if p.is_file() and not p.is_symlink()]
    except OSError:
        return []


def _remove(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except OSError:
        return False  # 사용 중·권한 없음 → 다음에 다시


def clean_temp_files(now: float | None = None) -> int:
    """1시간 넘은 임시 파일만 지운다 (지금 저장 중인 파일은 몇 초 안에 바뀌므로 건드리지 않음).
    수정 시각이 미래(PC 시계가 뒤로 감)면 남긴다."""
    now = time.time() if now is None else now
    removed = 0
    for chain in _temp_folders():
        for path in _plain_files(chain, "*.tmp"):
            try:
                age = now - path.stat().st_mtime
            except OSError:
                continue
            if age > TEMP_AGE_SECONDS and _remove(path):
                removed += 1
    return removed


def _backups(folder: Path, stem: str) -> list[Path]:
    return _plain_files((folder,), f"{stem}.broken*.json")


def prune_broken(stem: str, folder: Path | None = None, keep: Path | None = None) -> int:
    """'<stem>.broken*.json' 중 최근 KEEP_BROKEN 개만 남긴다 (수정 시각 기준, 폴더 기본 user_data).
    keep(방금 만든 백업)은 시각과 관계없이 항상 남기고 그 개수에 포함한다 (PC 시계가 뒤로 가도 지우지 않음)."""
    backups = []
    for path in _backups(folder or paths.USER_DIR, stem):
        if keep is not None and path == keep:
            continue
        try:
            backups.append((path.stat().st_mtime_ns, path.name, path))
        except OSError:
            continue
    backups.sort(reverse=True)
    room = KEEP_BROKEN - (1 if keep is not None else 0)
    return sum(1 for _, _, path in backups[room:] if _remove(path))


def has_backup(path: Path, raw: bytes) -> bool:
    """path 와 같은 폴더에 내용이 똑같은 '<이름>.broken*.json' 백업이 실제로 있는지.
    읽기만 하므로 링크로 옮긴 user_data 도 그대로 본다 (지우는 정리만 링크를 피함)."""
    try:
        backups = list(path.parent.glob(f"{path.stem}.broken*.json"))
    except OSError:
        return False
    for backup in backups:
        try:
            if backup.is_file() and backup.stat().st_size == len(raw) and backup.read_bytes() == raw:
                return True
        except OSError:
            continue
    return False


def clean_all() -> None:
    """켤 때 부름. 어떤 오류가 나도 프로그램 시작을 막지 않는다."""
    try:
        clean_temp_files()
        for stem in ("settings", "progress", "report"):
            prune_broken(stem)
    except Exception:
        pass

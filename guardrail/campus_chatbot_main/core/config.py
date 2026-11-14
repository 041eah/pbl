"""프로젝트 공통 경로 + DB 연결 헬퍼.
어느 폴더에서 실행해도 같은 DB·에셋을 찾도록 절대경로로 고정한다."""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = str(ROOT / "data" / "campus.db")
ASSETS_DIR = ROOT / "assets"

_wal_done = False


def connect(db_path: str = DB_PATH, row: bool = False) -> sqlite3.Connection:
    """DB 연결. 잠금(database is locked)에 강하도록 설정한다.

    - timeout=30초: 다른 연결이 쓰는 중이면 바로 실패하지 않고 최대 30초 기다린다.
      (OneDrive 동기화, 로그 뷰어 동시 실행 등으로 순간적으로 잠길 때 대비)
    - WAL 모드 + busy_timeout: 읽기와 쓰기가 서로 막지 않게 한다.
    """
    global _wal_done
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    if row:
        conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys = ON")
        if not _wal_done:
            conn.execute("PRAGMA journal_mode=WAL")
            _wal_done = True
    except sqlite3.Error:
        pass
    return conn

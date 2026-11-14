"""
앱 운영 데이터 저장소 — 대화 목록(최근 대화), 메시지, 동의 기록, 매니저 문의, 감사로그.

사이드바 '최근 대화'가 이제 실제로 저장/전환된다(새로고침해도 유지).
"""
import json
import sqlite3
import uuid
from datetime import datetime

from core.config import DB_PATH, connect

_APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS consents (
    owner_key TEXT PRIMARY KEY, name TEXT, phone TEXT, role TEXT, agreed_at TEXT, withdrawn_at TEXT);
CREATE TABLE IF NOT EXISTS conversations (
    conv_id TEXT PRIMARY KEY, owner_key TEXT, title TEXT, guard_level TEXT,
    created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS messages (
    msg_id INTEGER PRIMARY KEY AUTOINCREMENT, conv_id TEXT, role TEXT, content TEXT,
    refs_json TEXT, meta_json TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS inquiries (
    inquiry_id INTEGER PRIMARY KEY AUTOINCREMENT, owner_key TEXT, conv_id TEXT,
    content TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS audit_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, session_id TEXT, owner_key TEXT,
    role TEXT, guard_level TEXT, conv_id TEXT, stage TEXT, decision TEXT,
    query TEXT, detail_json TEXT);
"""


def _conn():
    conn = connect(row=True)
    return conn


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def init():
    """앱 시작 시 호출. 학사 DB가 아직 없으면(python database.py 를 안 돌렸으면) 자동으로 생성한다."""
    with _conn() as conn:
        has_students = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='students'").fetchone()
    if not has_students:
        import database          # 루트의 database.py (seed 고정 → 수동 생성과 동일한 DB)
        database.build()
    with _conn() as conn:
        conn.executescript(_APP_SCHEMA)


# ---------------- 동의 ----------------
def save_consent(owner_key, name, phone, role):
    with _conn() as conn:
        conn.execute("INSERT OR REPLACE INTO consents VALUES (?,?,?,?,?,NULL)",
                     (owner_key, name, phone, role, now()))


def get_consent(owner_key):
    with _conn() as conn:
        r = conn.execute("SELECT * FROM consents WHERE owner_key=? AND withdrawn_at IS NULL",
                         (owner_key,)).fetchone()
    return dict(r) if r else None


def withdraw(owner_key):
    """개인정보 삭제 및 이용 종료: 대화·메시지·문의 삭제 + 동의 철회 기록."""
    with _conn() as conn:
        conv_ids = [r[0] for r in conn.execute(
            "SELECT conv_id FROM conversations WHERE owner_key=?", (owner_key,))]
        conn.executemany("DELETE FROM messages WHERE conv_id=?", [(c,) for c in conv_ids])
        conn.execute("DELETE FROM conversations WHERE owner_key=?", (owner_key,))
        conn.execute("DELETE FROM inquiries WHERE owner_key=?", (owner_key,))
        conn.execute("UPDATE consents SET withdrawn_at=?, name=NULL, phone=NULL WHERE owner_key=?",
                     (now(), owner_key))
    return len(conv_ids)


# ---------------- 대화 ----------------
def create_conversation(owner_key, title, guard_level) -> str:
    conv_id = uuid.uuid4().hex[:12]
    t = now()
    with _conn() as conn:
        conn.execute("INSERT INTO conversations VALUES (?,?,?,?,?,?)",
                     (conv_id, owner_key, title, guard_level, t, t))
    return conv_id


def list_conversations(owner_key, limit=30):
    with _conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM conversations WHERE owner_key=? ORDER BY updated_at DESC LIMIT ?",
            (owner_key, limit))]


def get_conversation(conv_id):
    with _conn() as conn:
        r = conn.execute("SELECT * FROM conversations WHERE conv_id=?", (conv_id,)).fetchone()
    return dict(r) if r else None


def set_title(conv_id, title):
    with _conn() as conn:
        conn.execute("UPDATE conversations SET title=? WHERE conv_id=?", (title, conv_id))


def delete_all_conversations(owner_key):
    with _conn() as conn:
        ids = [(r[0],) for r in conn.execute(
            "SELECT conv_id FROM conversations WHERE owner_key=?", (owner_key,))]
        conn.executemany("DELETE FROM messages WHERE conv_id=?", ids)
        conn.execute("DELETE FROM conversations WHERE owner_key=?", (owner_key,))


def add_message(conv_id, role, content, refs=None, meta=None, created_at=None):
    t = created_at or now()
    with _conn() as conn:
        conn.execute("INSERT INTO messages (conv_id, role, content, refs_json, meta_json, created_at) "
                     "VALUES (?,?,?,?,?,?)",
                     (conv_id, role, content, json.dumps(refs or [], ensure_ascii=False),
                      json.dumps(meta or {}, ensure_ascii=False), t))
        conn.execute("UPDATE conversations SET updated_at=? WHERE conv_id=?", (t, conv_id))


def get_messages(conv_id):
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM messages WHERE conv_id=? ORDER BY msg_id", (conv_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["refs"] = json.loads(d.pop("refs_json") or "[]")
        d["meta"] = json.loads(d.pop("meta_json") or "{}")
        out.append(d)
    return out


# ---------------- 매니저 문의 ----------------
def add_inquiry(owner_key, conv_id, content):
    with _conn() as conn:
        conn.execute("INSERT INTO inquiries (owner_key, conv_id, content, created_at) VALUES (?,?,?,?)",
                     (owner_key, conv_id, content, now()))


# ---------------- 감사로그 ----------------
def audit(session_id, user, guard_level, conv_id, stage, decision, query, detail=None):
    """모든 가드레일 단계가 판정 결과를 여기에 남긴다(논문 7단계 마지막 '감사로그')."""
    with _conn() as conn:
        conn.execute(
            "INSERT INTO audit_log (ts, session_id, owner_key, role, guard_level, conv_id, stage, "
            "decision, query, detail_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (now(), session_id, getattr(user, "owner_key", None), getattr(user, "role", None),
             guard_level, conv_id, stage, decision, query,
             json.dumps(detail or {}, ensure_ascii=False, default=str)))

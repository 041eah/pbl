"""
사용자 식별(로그인) + 정책엔진이 쓸 '요청자 Context' 모듈 — 팀 스키마(users) 기준.

슈리챗 '개인 정보 입력' 모달에서 [이름] + [학번(교직원은 사번)]을 받는다.

  학생   : students.name + students.student_id 가 일치 → users.subject_student_id 로 계정 연결
  교직원 : users.user_id(또는 username) 로 식별 (팀 스키마 users에 이름 컬럼이 없으므로
           이름은 표시용으로만 쓰고 검증하지 않음)
  그 외  : guest (E4에서는 D0 공개정보만 허용 예정)

※ 역할(role)은 항상 users 테이블 조회 결과로만 정해진다. 대화 중 "나 교수야"라고 해도 바뀌지 않음 (T2 대응 전제).
※ 이름+학번은 '식별'이지 강한 '인증'이 아니다 → 논문 위협모델/한계에 "인증된 사용자 가정" 명시 필요.
"""
import sqlite3
from dataclasses import asdict, dataclass
from typing import Optional

from core.config import DB_PATH, connect

ROLE_LABELS = {"STUDENT": "학생", "ADVISOR": "지도교수", "STAFF": "학사지원팀", "ADMIN": "관리자"}


@dataclass
class AuthUser:
    role: str                              # STUDENT | ADVISOR | STAFF | ADMIN
    owner_key: str                         # 대화·동의 기록 소유자 키 (= users.user_id)
    name: str                              # 표시 이름
    login_id: str                          # 모달에 입력한 학번/사번
    user_id: Optional[str] = None          # users.user_id
    subject_student_id: Optional[str] = None  # 학생 계정의 본인 학번
    department: Optional[str] = None

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)

    # 이전 코드 호환용
    @property
    def student_id(self) -> Optional[str]:
        return self.subject_student_id

    @property
    def phone(self) -> str:
        return self.login_id

    def to_dict(self) -> dict:
        return asdict(self)


def _conn():
    conn = connect(row=True)
    return conn


def identify(name: str, login_id: str) -> Optional[AuthUser]:
    """이름 + 학번(교직원은 사번)으로 식별. DB에 없으면 None(로그인 거부). 게스트 역할은 없음."""
    name = (name or "").strip()
    login_id = (login_id or "").strip()
    with _conn() as conn:
        # 학생: students.name + 학번으로 확인, 표시 이름(AuthUser.name)은 users.name 사용
        r = conn.execute(
            "SELECT u.user_id, u.name AS uname, u.department, s.student_id FROM users u "
            "JOIN students s ON s.student_id = u.subject_student_id "
            "WHERE u.role='STUDENT' AND u.active=1 AND s.student_id=? AND s.name=?",
            (login_id, name)).fetchone()
        if r:
            return AuthUser("STUDENT", r["user_id"], r["uname"], login_id, r["user_id"],
                            r["student_id"], r["department"])
        # 교직원: users.name + 사번(user_id)이 둘 다 일치해야 식별
        r = conn.execute(
            "SELECT user_id, name, role, department FROM users "
            "WHERE role != 'STUDENT' AND active=1 AND UPPER(user_id)=UPPER(?) AND name=?",
            (login_id, name)).fetchone()
        if r:
            return AuthUser(r["role"], r["user_id"], r["name"], login_id, r["user_id"],
                            None, r["department"])
    return None


def personas() -> list[dict]:
    """연구용 설정 패널의 '페르소나 빠른 전환' 목록 (database.py의 고정 페르소나)."""
    from database import FIXED_STAFF_USERS, FIXED_STUDENTS
    out = [{"label": f"학생 · {n} ({sid}, {d})", "name": n, "login_id": sid}
           for sid, n, d, *_ in FIXED_STUDENTS]
    out += [{"label": f"{ROLE_LABELS[role]} · {name} ({uid}, {dept})", "name": name, "login_id": uid}
            for uid, name, role, dept, _ in FIXED_STAFF_USERS]
    return out


# ---------------------------------------------------------------------------
# 정책엔진 P(u,q,d) 재료 — E4 구현 시 사용
# ---------------------------------------------------------------------------
def relationship(user: AuthUser, subject_student_id: str) -> str:
    """A_subject 판정: 요청자와 정보주체(학생)의 관계 → access_policies.subject_scope 값으로 반환.

    SELF | ASSIGNED_STUDENT | ANY | NONE   (NONE은 매칭 정책이 없어 deny-by-default로 차단됨)
    지도교수는 담당(active) 학생만 접근 — 같은 학과라도 비담당이면 NONE.
    """
    with _conn() as conn:
        if user.role == "STUDENT":
            return "SELF" if user.subject_student_id == subject_student_id else "NONE"
        if user.role == "ADVISOR":
            r = conn.execute("SELECT 1 FROM advisor_assignments WHERE advisor_user_id=? AND student_id=? "
                             "AND active=1", (user.user_id, subject_student_id)).fetchone()
            return "ASSIGNED_STUDENT" if r else "NONE"
        if user.role in ("STAFF", "ADMIN"):
            return "ANY"
    return "NONE"


def policies_for(user: AuthUser, resource_id: str) -> list[dict]:
    """access_policies에서 (역할, 자원)에 해당하는 정책 목록."""
    with _conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM access_policies WHERE role=? AND resource_id=?", (user.role, resource_id))]


QUASI_IDENTIFIERS = ("department", "grade_year", "admission_year", "enrollment_status")


def group_size(**filters) -> int:
    """간접 재식별 통제 |S_q| < k 판정 재료: 준식별자 조건을 만족하는 학생 수.

    예) group_size(department="식품생명공학과", grade_year=5) → 1  (k=5면 차단 대상)
    """
    cols = [k for k in filters if k in QUASI_IDENTIFIERS]
    where = " AND ".join(f"{c}=?" for c in cols) or "1=1"
    with _conn() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM students WHERE {where}",
                            [filters[c] for c in cols]).fetchone()[0]

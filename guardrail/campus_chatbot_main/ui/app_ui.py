"""
슈리챗(SWU 학사 챗봇) UI 재현 — 모든 비교환경(E0~E4)이 공유하는 화면.

chatbot_baseline.py / chatbot_guardrail.py는 run_app()만 호출하고,
실제 응답 로직은 pipeline.run_turn()이 guard_level에 따라 바꿔 끼운다.

화면 구성(참고 스크린샷 기준)
  ① 홈: 빨강→연분홍 그라데이션, 프로필, 인사문구, 큰 입력카드, FAQ 카드
  ② 채팅: 회색 사이드바/상단바 + 흰 패널, 날짜, 봇/유저 말풍선, 시간, 매니저 문의, 하단 고정 입력창
  ③ 답변 생성 중: '···' + '문의 내용을 정리하고 있어요.' → '답변에 필요한 내용을 고르고 있어요.'
  ④ 답변: 인용번호 뱃지, '참고 자료 N개', 노란 안내 박스
  ⑤ 우상단 ··· 메뉴: 자동 번역 설정(비활성) / 대화방 나가기
  ⑥ 설정(톱니) → 개인정보 관리 → 전문 보기 / 개인정보 삭제 및 이용 종료
  ⑦ 첫 질문 시 '개인 정보 입력' 모달(이름·학번(교직원은 사번)·필수 동의) = 로그인/식별
"""
import base64
import html
import os
import re
import sys
import uuid
from datetime import datetime

# ui/app_ui.py를 직접 실행(streamlit run ui/app_ui.py)해도 프로젝트 루트의 core/를 찾도록 보정.
# 권장 실행법은 루트에서 `streamlit run chatbot_baseline.py`.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import markdown as md
import streamlit as st

from core import auth, llm, pipeline, store
from core.config import ASSETS_DIR

BOT_NAME = "슈리CHAT"
AVATAR_PATH = ASSETS_DIR / "profile.png"

HOME_PLACEHOLDER = "등록금 납부 기간은 언제"
CHAT_PLACEHOLDER = "AI에게 질문을 남겨주세요"
DISCLAIMER = "AI는 실수를 할 수 있습니다. 중요한 정보는 반드시 다시 확인해주세요."
WARN_TEXT = ("안내 : AI 답변은 참고용입니다. 중요 사항은 반드시 학교 공식 시스템이나 "
             "학과 사무실에서 직접 재확인하시기 바랍니다.")
NOTICE_TEXT = (
    "● 학사정보, 학교생활 정보를 쉽고 빠르게 알려드려요. 궁금한 점은 언제든지 물어보세요.\n"
    "● AI 답변은 참고용이며 법적효력이 없습니다. 중요한 사항은 반드시 학칙·규정·모집요강 및 담당부서 안내를 확인해야 합니다.\n"
    "● AI 특성상 오류나 최신정보 미반영 가능성이 있으므로 중요한 의사결정은 챗봇 답변에만 의존하지 말고 담당부서의 최종 안내를 확인해야 합니다.\n"
    "● 추가 문의는 '문의 글 남기기'를 이용하면 담당부서에서 확인 후 답변합니다.\n"
    "● 서비스 품질 향상을 위해 대화 내용이 보관되고 있습니다."
)
CONSENT_TEXT = [
    ("1. 개인정보 수집 및 이용 목적", "고객 상담, 서비스 지원 및 이용 기록 확인"),
    ("2. 수집 항목", "[필수] 이름, 학번(교직원은 사번), 기기 정보 (IP 주소, 브라우저 정보 등)"),
    ("3. 보유 기간", "동의 철회 시 파기<br>※ 귀하는 위 개인정보 수집·이용에 대한 동의를 거부할 수 있으며, "
                  "[필수] 항목에 대한 동의를 거부할 경우, 서울여자대학교 (학사용) 이용이 제한됩니다."),
]
FAQ = [
    ("[문의 글 남기기] 이용 방법",
     "챗봇 답변으로 해결되지 않은 내용은 대화 화면 오른쪽 아래 **매니저 문의 남기기** 버튼을 눌러 남겨 주세요.\n\n"
     "담당부서에서 확인 후 답변하며, 학교에 등록된 연락처로 답변 알림을 보내드려요."),
    ("슈리챗 답변 오류 신고 및 개선의견 접수 안내",
     "답변 오류나 개선 의견은 **매니저 문의 남기기**로 알려 주세요. 어떤 질문에 어떤 답변이 나왔는지 함께 적어 주시면 "
     "더 빠르게 확인할 수 있어요."),
    ("정확한 챗봇답변을 위해 어떻게 질문해야 하나요?",
     "- 한 번에 하나의 주제로 질문해 주세요.\n- 학년·학기·전공 등 조건을 함께 적어 주세요.\n"
     "- 예: \"2026학년도 2학기 등록금 추가 납부 기간 알려줘\""),
    ("수험생 입시 관련 질의는 응답하지 않습니다.",
     "슈리챗은 재학생 학사 상담용 서비스예요. 입시 관련 문의는 입학처 누리집을 이용해 주세요."),
    ("Foreign Language Settings",
     "You can ask questions in English. Automatic translation settings will be available soon."),
]


# =============================================================================
# 상태
# =============================================================================
def _init_state(default_level):
    ss = st.session_state
    ss.setdefault("sid", uuid.uuid4().hex[:10])
    ss.setdefault("user", None)          # auth.AuthUser | None
    ss.setdefault("view", "home")        # home | chat
    ss.setdefault("conv_id", None)
    ss.setdefault("pending", None)       # {"query":..., "attachments":[...]}
    ss.setdefault("dialog", None)        # (name, arg)
    ss.setdefault("guard_level", default_level)
    ss.setdefault("last_debug", None)
    ss.setdefault("toast", None)


def _open(name, arg=None):
    st.session_state.dialog = (name, arg)


def _go_home():
    st.session_state.view = "home"
    st.session_state.conv_id = None


def _new_chat():
    st.session_state.view = "chat"
    st.session_state.conv_id = None


def _open_conv(conv_id):
    st.session_state.view = "chat"
    st.session_state.conv_id = conv_id


def _submit(text, files=None):
    text = (text or "").strip()
    if not text:
        return
    attachments = []
    for f in files or []:
        try:
            attachments.append({"name": f.name, "text": f.getvalue().decode("utf-8", errors="ignore")[:6000]})
        except Exception:
            pass
    st.session_state.pending = {"query": text, "attachments": attachments}
    if st.session_state.user is None:
        _open("info")   # 슈리챗처럼 첫 질문 시 '개인 정보 입력' 모달 (현재 화면 위에 뜸)
    elif st.session_state.view == "home":
        st.session_state.view = "chat"
        st.session_state.conv_id = None
    st.rerun()


# =============================================================================
# 렌더링 헬퍼
# =============================================================================
def _avatar_b64():
    return base64.b64encode(AVATAR_PATH.read_bytes()).decode() if AVATAR_PATH.exists() else ""


def _hhmm(iso):
    return datetime.fromisoformat(iso).strftime("%H:%M")


def _md_to_html(text):
    safe = text.replace("<", "&lt;")                     # LLM/문서 속 HTML(주석 인젝션 등) 무력화
    out = md.markdown(safe, extensions=["extra", "sane_lists", "nl2br"])
    return re.sub(r"\s?\[(\d{1,2})\]", r'<span class="cite">\1</span>', out)


def _bot_html(content, ts, refs=None, warn=False, label=True):
    refs_html = ""
    if refs:
        items = "".join(
            f'<li><span class="cite">{r["n"]}</span><a href="{html.escape(r["url"])}" target="_blank">'
            f'{html.escape(r["title"])}</a>{" · 게시판" if r.get("source") == "board" else ""}</li>'
            for r in refs)
        refs_html = (f'<details class="refs"><summary>참고 자료 {len(refs)}개'
                     f'<span class="ref-icons"><i></i><i></i><i></i></span></summary><ul>{items}</ul></details>')
    warn_html = f'<div class="warn"><span class="warn-ico">▲</span>{WARN_TEXT}</div>' if warn else ""
    lab = f'<div class="bot-label">{BOT_NAME}</div>' if label else ""
    return (f'<div class="row bot">{lab}<div class="bubble bot">{_md_to_html(content)}{refs_html}</div>'
            f'{warn_html}<div class="ts">{ts}</div></div>')


def _user_html(content, ts):
    body = html.escape(content).replace("\n", "<br>")
    return f'<div class="row user"><div class="bubble user">{body}</div><div class="ts">{ts}</div></div>'


def _typing_html(status):
    return (f'<div class="row bot"><div class="bubble bot typing"><div class="dots"><i></i><i></i><i></i></div>'
            f'<div class="shimmer">{html.escape(status)}</div></div></div>')


def _title_from_answer(text):
    first = next((l for l in text.splitlines() if l.strip()), "")
    return first.strip()[:40]


# =============================================================================
# CSS
# =============================================================================
C = {
    "grad_top": "#892927", "grad_bottom": "#e5d2d3", "app_bg": "#f6f7fb", "panel": "#ffffff",
    "bubble_bot": "#f1f2f7", "bubble_user": "#dbbebe", "text": "#242333", "label": "#8f92a4",
    "selected": "#e6e8f1", "border": "#c3c5d3", "icon_dim": "#d1d3e1", "placeholder": "#838383",
    "red": "#892827", "warn_bg": "#fcf8e2", "warn_text": "#cf8a32", "divider": "#f1f2f7",
    "row_bg": "#fbfbfd", "req_bg": "#f7e4e2", "req_text": "#c52d1e", "asterisk": "#de473d",
}

BASE_CSS = """
html, body, .stApp, .stMarkdown, p, button, input, textarea, label, summary, li, [data-testid="stDialog"] section[role="dialog"] {
  font-family: Pretendard, -apple-system, "Malgun Gothic", sans-serif !important;
}
.stApp { color: %(text)s; --sbw: 256px; }
.stApp:has([data-testid="stSidebar"][aria-expanded="false"]) { --sbw: 0px; }
/* Streamlit 기본 크롬 숨김 */
[data-testid="stDecoration"], [data-testid="stStatusWidget"], [data-testid="stToolbarActions"], [data-testid="stAppDeployButton"],
[data-testid="stMainMenu"], .stDeployButton, [data-testid="stLogoSpacer"] { display: none !important; }
/* 사이드바 열고닫기 버튼 → 슈리챗 패널 아이콘 모양으로 */
[data-testid="stSidebarHeader"] { position: absolute; top: 0; left: 0; height: 0; padding: 0 !important; }
[data-testid="stSidebarCollapseButton"], [data-testid="stExpandSidebarButton"] { position: fixed !important; top: 13px; left: 9px; z-index: 1001; display: block !important; visibility: visible !important; opacity: 1 !important; }
[data-testid="stSidebarCollapseButton"] button, [data-testid="stExpandSidebarButton"] { width: 32px; height: 32px; padding: 0 !important; background: transparent !important; border: none !important; }
[data-testid="stSidebarCollapseButton"] button > *, [data-testid="stExpandSidebarButton"] > * { display: none !important; }
[data-testid="stSidebarCollapseButton"] button, [data-testid="stExpandSidebarButton"] { position: relative; }
[data-testid="stSidebarCollapseButton"] button::before, [data-testid="stExpandSidebarButton"]::before { content: ""; position: absolute;
  left: 6px; top: 8px; width: 20px; height: 16px; border: 1.8px solid var(--panel-ico); border-radius: 4px; box-sizing: border-box; }
[data-testid="stSidebarCollapseButton"] button::after, [data-testid="stExpandSidebarButton"]::after { content: ""; position: absolute;
  left: 12px; top: 8px; width: 1.8px; height: 16px; background: var(--panel-ico); }
header[data-testid="stHeader"] { background: transparent !important; height: 50px; pointer-events: none; }
header[data-testid="stHeader"] * { pointer-events: auto; }
.stElementContainer:has(> .stHtml), .stElementContainer:has(style) { margin: 0 !important; }
/* 화면 전환 중 이전 화면 요소(흐리게 남는 stale 요소) 숨김 */
section.stMain [data-stale="true"] { display: none !important; }

/* ---------- 사이드바 ---------- */
section[data-testid="stSidebar"] { border-right: none !important; }
section[data-testid="stSidebar"][aria-expanded="true"] { width: 256px !important; min-width: 256px !important; }
section[data-testid="stSidebar"] > div { background: transparent; }
[data-testid="stSidebarContent"] { padding-top: 6px; }
[data-testid="stSidebarUserContent"] { padding: 57px 8px 80px 8px !important; }
[data-testid="stSidebarContent"] { padding-left: 0 !important; padding-right: 0 !important; }
[data-testid="stSidebarUserContent"] [data-testid="stVerticalBlock"] { gap: 2px !important; }
section[data-testid="stSidebar"] button {
  background: transparent !important; border: none !important; box-shadow: none !important;
  justify-content: flex-start !important; width: 100% !important; min-height: 38px; padding: 0 10px !important;
  border-radius: 8px !important;
}
section[data-testid="stSidebar"] button > div, section[data-testid="stSidebar"] button > div > span { width: 100%; justify-content: flex-start !important; }
section[data-testid="stSidebar"] button p { font-size: 14px !important; font-weight: 500; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; text-align: left; }
section[data-testid="stSidebar"] button [data-testid="stIconMaterial"] { font-size: 22px !important; margin-right: 4px; }
.st-key-nav_home button p, .st-key-nav_new button p { font-weight: 600 !important; }
.st-key-recent_head { margin-top: 8px; padding: 0 0 8px 10px; flex-wrap: nowrap !important; justify-content: space-between !important; align-items: center !important; justify-content: space-between; }
.st-key-recent_head p { font-size: 13.5px !important; margin: 0 !important; }
.st-key-recent_head button { min-height: 22px !important; height: 22px; margin-top: 6px; width: auto !important; padding: 0 4px !important; }
.st-key-recent_head [data-testid="stHorizontalBlock"] { align-items: center !important; justify-content: space-between !important; }
section[data-testid="stSidebar"] [class*="st-key-conv_"] button { min-height: 41px; }
section[data-testid="stSidebar"] [class*="st-key-conv_"] button p { font-weight: 400 !important; }
.st-key-gear { position: fixed; left: 16px; bottom: 16px; width: 44px !important; z-index: 100; }
.st-key-gear button { width: 40px !important; min-height: 40px; padding: 0 !important; justify-content: center !important; }
.st-key-gear button [data-testid="stIconMaterial"] { font-size: 24px !important; margin: 0 !important; }

/* ---------- 우상단 창 컨트롤 ---------- */
.st-key-winctl { position: fixed; top: 6px; right: 14px; z-index: 1000; width: auto !important;
  flex-direction: row !important; gap: 2px !important; }
.st-key-winctl button { background: transparent !important; border: none !important; box-shadow: none !important;
  width: 36px; height: 36px; min-height: 36px; padding: 0 !important; border-radius: 8px !important; }
.st-key-winctl button [data-testid="stIconMaterial"] { font-size: 22px !important; }
[data-testid="stPopoverButton"] > div > div[aria-hidden="true"] { display: none !important; }
[data-testid="stPopoverBody"] { padding: 6px !important; min-width: 280px; border-radius: 10px !important;
  box-shadow: 0 4px 18px rgba(30, 30, 60, .12) !important; border: none !important; }
[data-testid="stPopoverBody"] button { border: none !important; background: transparent !important; justify-content: flex-start !important;
  width: 100%% !important; min-height: 34px; }
[data-testid="stPopoverBody"] button p { font-size: 12px !important; font-weight: 700; color: %(text)s; }
[data-testid="stPopoverBody"] button:disabled p { color: #c9cad3 !important; font-weight: 500; }
[data-testid="stPopoverBody"] [data-testid="stVerticalBlock"] { gap: 0 !important; }

/* 입력창 포커스 시 빨간 테두리 제거 */
[data-testid="stChatInput"], [data-testid="stChatInput"]:focus-within { border-color: transparent !important; outline: none !important; box-shadow: none !important; }
/* 전송 버튼: 화살표 → 종이비행기(send) */
[data-testid="stChatInputSubmitButton"] { background: transparent !important; border: none !important; }
[data-testid="stChatInputSubmitButton"] svg { display: none !important; }
[data-testid="stChatInputSubmitButton"]::after { content: ""; width: 20px; height: 20px; background: #8f92a4;
  -webkit-mask: url("data:image/svg+xml;utf8,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 24 24%27%3E%3Cpath d=%27M3.4 20.4l17.45-7.48c.81-.35.81-1.49 0-1.84L3.4 3.6c-.66-.29-1.39.2-1.39.91L2 9.12c0 .5.37.93.87.99L17 12 2.87 13.88c-.5.07-.87.5-.87 1l.01 4.61c0 .71.73 1.2 1.39.91z%27/%3E%3C/svg%3E") center / contain no-repeat; mask: url("data:image/svg+xml;utf8,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 24 24%27%3E%3Cpath d=%27M3.4 20.4l17.45-7.48c.81-.35.81-1.49 0-1.84L3.4 3.6c-.66-.29-1.39.2-1.39.91L2 9.12c0 .5.37.93.87.99L17 12 2.87 13.88c-.5.07-.87.5-.87 1l.01 4.61c0 .71.73 1.2 1.39.91z%27/%3E%3C/svg%3E") center / contain no-repeat; }
[data-testid="stChatInputSubmitButton"]:disabled::after { background: %(icon_dim)s; }

/* ---------- 대화 ---------- */
.chat-wrap { max-width: 634px; margin: 0 auto; }
.date { text-align: center; font-size: 12.5px; font-weight: 600; color: %(label)s; margin: 6px 0 14px; }
.row { display: flex; flex-direction: column; margin-bottom: 18px; }
.row.bot { align-items: flex-start; }
.row.user { align-items: flex-end; }
.bot-label { font-size: 12.5px; font-weight: 600; color: %(label)s; margin: 0 0 6px 0; }
.bubble { font-size: 14px; line-height: 1.62; color: %(text)s; border-radius: 16px; word-break: keep-all; overflow-wrap: anywhere; }
.bubble.bot { background: %(bubble_bot)s; padding: 14px 16px; max-width: 446px; }
.bubble.user { background: %(bubble_user)s; padding: 11px 15px; max-width: 446px; }
.bubble p { margin: 0 0 12px 0 !important; font-size: 14px !important; }
.bubble p:last-child { margin-bottom: 0 !important; }
.bubble ul, .bubble ol { margin: 0 0 12px 0; padding-left: 22px; }
.bubble li { margin: 0 0 2px 0; font-size: 14px; }
.bubble strong { font-weight: 700; }
.bubble em { color: %(label)s; font-style: normal; font-size: 12.5px; }
.cite { display: inline-flex; align-items: center; justify-content: center; min-width: 17px; height: 17px;
  padding: 0 4px; margin: 0 2px 0 5px; border-radius: 9px; background: %(selected)s; font-size: 10.5px; font-weight: 700;
  color: #000; vertical-align: 1px; line-height: 1; }
.ts { font-size: 11.5px; color: %(label)s; margin-top: 8px; }
.refs { margin-top: 12px; }
.refs summary { list-style: none; display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
  background: %(selected)s; border-radius: 6px; padding: 4px 8px; font-size: 11px; font-weight: 700; color: %(text)s; }
.refs summary::-webkit-details-marker { display: none; }
.ref-icons i { display: inline-block; width: 11px; height: 11px; border-radius: 3px; margin-left: -3px; border: 1px solid #fff; }
.ref-icons i:nth-child(1) { background: #9aa6c9; } .ref-icons i:nth-child(2) { background: #f0b73e; } .ref-icons i:nth-child(3) { background: #b19ae8; }
.refs ul { margin: 8px 0 0 0 !important; padding-left: 0 !important; list-style: none; }
.refs li { font-size: 12.5px !important; margin-bottom: 4px; }
.refs a { color: %(text)s !important; text-decoration: underline; text-underline-offset: 2px; }
.warn { margin-top: 8px; background: %(warn_bg)s; color: %(warn_text)s; font-size: 12px; font-weight: 600;
  padding: 9px 14px; border-radius: 10px; max-width: 626px; }
.warn-ico { font-size: 11px; margin-right: 5px; }
.typing { padding: 14px 16px 14px 16px !important; min-width: 190px; }
.dots { display: flex; gap: 3px; margin: 2px 0 10px 10px; }
.dots i { width: 4px; height: 4px; border-radius: 50%%; background: #6b6d7c; animation: blink 1.2s infinite; }
.dots i:nth-child(2) { animation-delay: .2s; } .dots i:nth-child(3) { animation-delay: .4s; }
@keyframes blink { 0%%, 80%%, 100%% { opacity: .25; } 40%% { opacity: 1; } }
.shimmer { font-size: 14px; background: linear-gradient(90deg, #b8bac6 0%%, #b8bac6 30%%, %(text)s 50%%, #b8bac6 70%%, #b8bac6 100%%);
  background-size: 250%% 100%%; -webkit-background-clip: text; background-clip: text; color: transparent;
  animation: shimmer 1.8s linear infinite; }
@keyframes shimmer { from { background-position: 100%% 0; } to { background-position: -150%% 0; } }

/* ---------- 모달(st.dialog) ---------- */
[data-testid="stDialog"] > div { width: 372px !important; max-width: calc(100vw - 32px) !important; border-radius: 16px !important;
  margin: auto !important; overflow: hidden; box-shadow: 0 8px 30px rgba(30, 30, 60, .12) !important; }
[data-testid="stDialog"] section[role="dialog"] { width: 100% !important; max-width: 100% !important; padding: 0 !important; border-radius: 16px !important; }
[data-testid="stDialog"] section[role="dialog"] > h2 { display: none !important; }
[data-testid="stDialog"] section[role="dialog"] > button[aria-label="Close"] { position: absolute; top: 18px; right: 16px; width: 28px; height: 28px; z-index: 5; color: %(text)s; }
[data-testid="stDialog"] section[role="dialog"] > button[aria-label="Close"] svg { width: 15px; height: 15px; }
[data-testid="stDialog"] section[role="dialog"] > div:last-child { padding: 16px 16px 16px 16px !important; }
[data-testid="stDialog"] section[role="dialog"] [data-testid="stDialogHeader"], [data-testid="stDialog"] section[role="dialog"] h2 { min-height: 0 !important; }
[data-testid="stDialog"] section[role="dialog"] [data-testid="stVerticalBlock"] { gap: 10px; }
[data-testid="stDialog"] { background: rgba(190, 190, 208, .75) !important; }
.dlg-title { font-size: 18px; font-weight: 700; color: %(text)s; margin: 0; line-height: 28px; padding-right: 36px; word-break: keep-all; }
[data-testid="stDialog"] section[role="dialog"] .st-key-back { margin-right: -4px; }
.dlg-hr { height: 1px; background: %(divider)s; margin: 4px -16px 6px -16px; }
.dlg-label { font-size: 12.5px; color: %(label)s; font-weight: 500; margin: 6px 0 0 0; }
.dlg-text { font-size: 13px; line-height: 1.55; color: %(text)s; }
.dlg-text b { font-weight: 700; }
.consent-body h4 { font-size: 13px !important; font-weight: 700 !important; margin: 0 0 3px 0 !important; padding: 0 !important; color: %(text)s; }
.consent-body p { font-size: 13px !important; margin: 0 0 16px 0 !important; line-height: 1.5; color: #41404e; }
.agree-row { display: flex; justify-content: space-between; gap: 10px; padding: 6px 10px; }
.agree-row b { font-size: 13px; font-weight: 700; }
.agree-row p { font-size: 12px !important; color: #989bab; margin: 4px 0 0 0 !important; line-height: 1.5; }
.agree-row span { font-size: 11.5px; color: #b7b9c7; white-space: nowrap; padding-top: 22px; }
.field-label { font-size: 13px; font-weight: 600; color: %(text)s; margin: 4px 0 -4px 0; }
.field-label em { color: %(asterisk)s; font-style: normal; margin-left: 3px; }
.req-tag { background: %(req_bg)s; color: %(req_text)s; font-size: 10.5px; font-weight: 700; padding: 2px 7px; border-radius: 4px; margin-right: 6px; }
.agree-label { font-size: 12.5px; color: %(text)s; }
.agree-label u { text-underline-offset: 2px; }
.st-key-agree_row { gap: 0 !important; flex-wrap: nowrap !important; }
.st-key-agree_row .stCheckbox { margin-right: 6px; }
.st-key-agree_row .stElementContainer { width: auto !important; }
[data-testid="stDialog"] section[role="dialog"] .st-key-consent_link button { background: transparent !important; border: none !important;
  padding: 0 !important; min-height: 0 !important; height: auto; box-shadow: none !important; }
[data-testid="stDialog"] section[role="dialog"] .st-key-consent_link button p { font-size: 12.5px !important; font-weight: 400 !important;
  color: %(text)s !important; text-decoration: underline; text-underline-offset: 2px; white-space: nowrap; }
.sec-label { font-size: 12.5px; color: %(label)s; margin: 10px 0 -4px 2px; }
[data-testid="stDialog"] section[role="dialog"] button[kind="primary"] { background: %(red)s !important; border: none !important; min-height: 44px;
  border-radius: 22px !important; }
[data-testid="stDialog"] section[role="dialog"] button[kind="primary"] p { color: #fff !important; font-weight: 700 !important; font-size: 14px !important; }
[data-testid="stDialog"] section[role="dialog"] button[kind="secondary"] { background: #fff !important; border: 1px solid %(border)s !important;
  min-height: 42px; border-radius: 22px !important; }
[data-testid="stDialog"] section[role="dialog"] button[kind="secondary"] p { font-weight: 700 !important; font-size: 14px !important; color: %(text)s; }
[data-testid="stDialog"] section[role="dialog"] button:disabled { background: %(divider)s !important; border: none !important; }
[data-testid="stDialog"] section[role="dialog"] button:disabled p { color: %(icon_dim)s !important; }
[data-testid="stDialog"] section[role="dialog"] .st-key-back button, [data-testid="stDialog"] section[role="dialog"] [class*="st-key-x_"] button {
  background: transparent !important; border: none !important; min-height: 30px !important; width: 30px; padding: 0 !important; }
[data-testid="stDialog"] section[role="dialog"] .st-key-back button [data-testid="stIconMaterial"], [data-testid="stDialog"] section[role="dialog"] [class*="st-key-x_"] button [data-testid="stIconMaterial"] { font-size: 24px !important; color: %(text)s; }
[data-testid="stDialog"] section[role="dialog"] [class*="st-key-row_"] button { background: %(row_bg)s !important; border: 1px solid #e6e5e9 !important;
  border-radius: 8px !important; min-height: 46px; justify-content: space-between !important; padding: 0 12px !important; }
[data-testid="stDialog"] section[role="dialog"] [class*="st-key-row_"] button p { font-size: 13.5px !important; font-weight: 700 !important; }
[data-testid="stDialog"] section[role="dialog"] [class*="st-key-row_"] button > div, [data-testid="stDialog"] section[role="dialog"] [class*="st-key-row_"] button > div > span { width: 100%; justify-content: flex-start !important; }
[data-testid="stDialog"] section[role="dialog"] [class*="st-key-row_"] button::after { content: "chevron_right"; font-family: "Material Symbols Rounded"; font-size: 20px; color: %(text)s; margin-left: auto; }
[data-testid="stDialog"] section[role="dialog"] [class*="st-key-flat_"] button { background: transparent !important; border: none !important;
  justify-content: space-between !important; padding: 0 4px !important; min-height: 44px; border-radius: 8px !important; }
[data-testid="stDialog"] section[role="dialog"] [class*="st-key-flat_"] button p { font-size: 14px !important; font-weight: 600 !important; }
[data-testid="stDialog"] section[role="dialog"] .st-key-del button { border-radius: 8px !important; border-color: #d9dae3 !important; min-height: 38px; }
[data-testid="stDialog"] section[role="dialog"] .st-key-del button p { font-size: 13px !important; }
[data-testid="stDialog"] section[role="dialog"] input { font-size: 14px !important; }
[data-testid="stDialog"] section[role="dialog"] [data-testid="stTextInputRootElement"] { border-radius: 8px !important; border: 1px solid %(border)s !important;
  height: 40px; background: #fff !important; }
[data-testid="stDialog"] section[role="dialog"] [data-testid="stTextInputRootElement"]:focus-within { border: 2px solid %(text)s !important; box-shadow: 0 0 0 3px #d5d8e4 !important; }
[data-testid="stDialog"] section[role="dialog"] [data-testid="stTextInputRootElement"] > div { background: #fff !important; }
[data-testid="stDialog"] section[role="dialog"] [data-testid="stTextInputRootElement"] input { background: #fff !important; }
[data-testid="stDialog"] section[role="dialog"] textarea { font-size: 14px !important; }
"""

HOME_CSS = """
.stApp { background: linear-gradient(180deg, %(grad_top)s 0%%, %(grad_bottom)s 100%%) !important; --panel-ico: #fff; }
section[data-testid="stSidebar"] { background: transparent !important; }
section[data-testid="stSidebar"] button p, section[data-testid="stSidebar"] [data-testid="stIconMaterial"] { color: #fff !important; }
section[data-testid="stSidebar"] button:hover { background: rgba(255,255,255,.08) !important; }
.st-key-recent_head p { color: rgba(255,255,255,.55) !important; }
.st-key-winctl [data-testid="stIconMaterial"] { color: #fff !important; }
section.stMain { background: transparent !important; }
[data-testid="stMainBlockContainer"] { max-width: 653px !important; padding: 0 16px 60px !important;
  margin-left: max(0px, calc(50vw - var(--sbw) - 326px)) !important; margin-right: auto !important; }
.hero { text-align: center; padding-top: 108px; }
.hero img { width: 72px; height: 72px; border-radius: 50%%; box-shadow: 0 1px 4px rgba(0,0,0,.12); }
.hero h1 { color: #fff !important; font-size: 28px !important; font-weight: 800 !important; line-height: 1.38 !important;
  margin: 18px 0 36px !important; padding: 0 !important; letter-spacing: -0.3px; }
.st-key-home_input [data-testid="stChatInput"] { background: #fff !important; border: none !important; border-radius: 20px !important;
  min-height: 0; padding: 0 !important; box-shadow: none !important; }
.st-key-home_input [data-testid="stChatInput"] > div, .st-key-home_input [data-testid="stChatInput"] > div > div { background: transparent !important; height: 100%%; align-items: stretch; }
.st-key-home_input textarea { min-height: 116px !important; height: 116px; padding: 10px 22px 0 0 !important; font-size: 13.5px !important; color: %(text)s !important;
  background: transparent !important; }
.st-key-home_input textarea::placeholder { color: %(placeholder)s !important; opacity: 1; }
.st-key-home_input [data-testid="stChatInputSubmitButton"] { align-self: flex-end; margin: 0 -2px 2px 0; width: 32px; height: 32px; }
.st-key-faq { background: #fff; border-radius: 20px; padding: 22px 22px 16px 22px; margin-top: 36px; gap: 0 !important; }
.st-key-faq button > div, .st-key-faq button > div > span, [data-testid="stDialog"] section[role="dialog"] [class*="st-key-flat_"] button > div, [data-testid="stDialog"] section[role="dialog"] [class*="st-key-flat_"] button > div > span { width: 100%%; justify-content: space-between !important; }
.faq-title { font-size: 14px; font-weight: 700; margin: 0 0 8px 0; color: %(text)s; }
.st-key-faq button { background: transparent !important; border: none !important; border-bottom: 1px solid %(divider)s !important;
  border-radius: 0 !important; min-height: 36px; height: 36px; padding: 0 !important; justify-content: space-between !important; width: 100%% !important; box-shadow: none !important; }
.st-key-faq [class*="st-key-faq_4"] button { border-bottom: none !important; }
.st-key-faq button p { font-size: 12.5px !important; font-weight: 400 !important; color: %(text)s; }
.st-key-faq button [data-testid="stIconMaterial"] { font-size: 20px !important; color: %(text)s; }
"""

CHAT_CSS = """
.stApp { background: %(app_bg)s !important; --panel-ico: %(text)s; }
section[data-testid="stSidebar"] { background: %(app_bg)s !important; }
section[data-testid="stSidebar"] button p, section[data-testid="stSidebar"] [data-testid="stIconMaterial"] { color: %(text)s !important; }
section[data-testid="stSidebar"] button:hover { background: %(selected)s !important; }
section[data-testid="stSidebar"] button[kind="primary"] { background: %(selected)s !important; }
.st-key-recent_head p { color: %(label)s !important; }
.st-key-winctl [data-testid="stIconMaterial"] { color: %(text)s !important; }
section.stMain { background: %(panel)s !important; margin-top: 50px; border-top-left-radius: 14px;
  height: calc(100vh - 50px) !important; max-height: calc(100vh - 50px); }
[data-testid="stMainBlockContainer"] { max-width: 666px !important; padding: 0 16px 24px !important; }
[data-testid="stMainBlockContainer"] > div > [data-testid="stVerticalBlock"] { gap: 0 !important; }
[data-testid="stBottom"] > div, [data-testid="stBottomBlockContainer"] { background: %(panel)s !important; }
[data-testid="stBottomBlockContainer"] { max-width: 666px !important; padding: 8px 16px 8px !important; }
[data-testid="stBottomBlockContainer"] [data-testid="stVerticalBlock"] { gap: 12px; }
.st-key-mgr_row { align-items: flex-end; }
.st-key-mgr_row button { background: #fff !important; border: 1px solid %(border)s !important; border-radius: 22px !important;
  min-height: 44px; padding: 0 15px !important; box-shadow: none !important; }
.st-key-mgr_row button p { font-size: 14px !important; font-weight: 600 !important; color: %(text)s; }
[data-testid="stBottom"], [data-testid="stBottom"] > div { background: %(panel)s !important; }
[data-testid="stBottom"] [data-testid="stChatInput"] { background: %(bubble_bot)s !important; border: none !important;
  border-radius: 16px !important; box-shadow: none !important; padding: 0 !important; }
[data-testid="stBottom"] [data-testid="stChatInput"] > div { width: 100%% !important; background: transparent !important; padding: 10px 12px 8px 4px !important; }
[data-testid="stBottom"] [data-testid="stChatInput"] > div > div { flex-wrap: wrap !important; width: 100%% !important; align-items: center; }
[data-testid="stBottom"] [data-testid="stChatInput"] > div > div > div:first-child { flex: 0 0 100%% !important; order: 0; }
[data-testid="stBottom"] [data-testid="stChatInput"] > div > div > div:has([data-testid="stChatInputFileUploadButton"]) { order: 1; margin-left: 10px; }
[data-testid="stBottom"] [data-testid="stChatInput"] > div > div > div:has([data-testid="stChatInputSubmitButton"]) { order: 2; margin-left: auto; }
[data-testid="stBottom"] [data-testid="stChatInput"] textarea { background: transparent !important; font-size: 13.5px !important;
  min-height: 34px !important; color: %(text)s !important; padding: 4px 10px 0 10px !important; }
[data-testid="stBottom"] [data-testid="stChatInput"] textarea::placeholder { color: #757575 !important; opacity: 1; }
[data-testid="stChatInputFileUploadButton"] button { background: transparent !important; }
[data-testid="stChatInputFileUploadButton"] button svg { display: none !important; }
[data-testid="stChatInputFileUploadButton"] button::after { content: ""; display: block; width: 18px; height: 18px; background: %(icon_dim)s;
  -webkit-mask: url("data:image/svg+xml;utf8,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 24 24%27%3E%3Cpath d=%27M16.5 6v11.5c0 2.21-1.79 4-4 4s-4-1.79-4-4V5c0-1.38 1.12-2.5 2.5-2.5s2.5 1.12 2.5 2.5v10.5c0 .55-.45 1-1 1s-1-.45-1-1V6H10v9.5c0 1.38 1.12 2.5 2.5 2.5s2.5-1.12 2.5-2.5V5c0-2.21-1.79-4-4-4S7 2.79 7 5v12.5c0 3.04 2.46 5.5 5.5 5.5s5.5-2.46 5.5-5.5V6h-1.5z%27/%3E%3C/svg%3E") center / contain no-repeat; mask: url("data:image/svg+xml;utf8,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 24 24%27%3E%3Cpath d=%27M16.5 6v11.5c0 2.21-1.79 4-4 4s-4-1.79-4-4V5c0-1.38 1.12-2.5 2.5-2.5s2.5 1.12 2.5 2.5v10.5c0 .55-.45 1-1 1s-1-.45-1-1V6H10v9.5c0 1.38 1.12 2.5 2.5 2.5s2.5-1.12 2.5-2.5V5c0-2.21-1.79-4-4-4S7 2.79 7 5v12.5c0 3.04 2.46 5.5 5.5 5.5s5.5-2.46 5.5-5.5V6h-1.5z%27/%3E%3C/svg%3E") center / contain no-repeat; }
.disclaimer p { text-align: center; font-size: 10px !important; color: #969baa; margin: -6px 0 0 0 !important; }
[data-testid="stBottomBlockContainer"] .stElementContainer:has(.disclaimer) { margin-bottom: 0; }
"""


def _inject_css(view):
    css = BASE_CSS + (HOME_CSS if view == "home" else CHAT_CSS)
    css = re.sub(r"%\((\w+)\)s", lambda m: C[m.group(1)], css).replace("%%", "%")
    st.html(f"<style>{css}</style>")


# =============================================================================
# 공통 레이아웃
# =============================================================================
def _sidebar():
    ss = st.session_state
    with st.sidebar:
        st.button("홈", icon=":material/home:", key="nav_home", on_click=_go_home, width="stretch")
        st.button("새로운 대화하기", icon=":material/send:", key="nav_new", on_click=_new_chat, width="stretch")
        with st.container(horizontal=True, key="recent_head"):
            st.markdown("최근 대화")
            with st.popover("", icon=":material/more_horiz:", type="tertiary"):
                if st.button("최근 대화 전체 삭제", key="del_all", disabled=ss.user is None):
                    store.delete_all_conversations(ss.user.owner_key)
                    _go_home()
                    st.rerun()
        if ss.user:
            for c in store.list_conversations(ss.user.owner_key):
                selected = ss.view == "chat" and ss.conv_id == c["conv_id"]
                st.button(c["title"] or "새 대화", key=f"conv_{c['conv_id']}", width="stretch",
                          type="primary" if selected else "secondary",
                          on_click=_open_conv, args=(c["conv_id"],))
        with st.container(key="gear"):
            st.button("", icon=":material/settings:", key="gear_btn", on_click=_open, args=("settings",))


def _window_controls():
    with st.container(key="winctl", horizontal=True):
        if st.session_state.view == "chat":
            with st.popover("", icon=":material/more_horiz:", type="tertiary"):
                st.button("자동 번역 설정", key="mt_translate", disabled=True)
                st.button("대화방 나가기", key="mt_leave", on_click=_go_home)
        st.button("", icon=":material/close_fullscreen:", key="win_min", on_click=_go_home)
        st.button("", icon=":material/close:", key="win_close", on_click=_go_home)


# =============================================================================
# ① 홈
# =============================================================================
def _home():
    st.html(f'<div class="hero"><img src="data:image/png;base64,{_avatar_b64()}">'
            f'<h1>안녕하세요. 슈리챗이에요.<br>지금은 베타서비스 중이에요.</h1></div>')
    with st.container(key="home_input"):
        v = st.chat_input(HOME_PLACEHOLDER, key="home_chat")
    if v:
        _submit(v)
    with st.container(key="faq"):
        st.html('<div class="faq-title">FAQ</div>')
        for i, (title, _) in enumerate(FAQ):
            st.button(title, key=f"faq_{i}", icon=":material/chevron_right:", icon_position="right",
                      on_click=_open, args=("faq", i), width="stretch")


# =============================================================================
# ② 채팅
# =============================================================================
def _chat():
    ss = st.session_state

    # --- 하단 고정 영역(매니저 문의 + 입력창 + 안내문) — 먼저 그려서 답변 생성 중에도 보이게 ---
    with st.bottom:
        with st.container(key="mgr_row", horizontal=True, horizontal_alignment="right"):
            st.button("매니저 문의 남기기", key="mgr_btn", on_click=_open, args=("manager",))
        v = st.chat_input(CHAT_PLACEHOLDER, key="chat_in", accept_file="multiple",
                          file_type=["txt", "md"])
        st.markdown(f'<div class="disclaimer">\n\n{DISCLAIMER}\n\n</div>', unsafe_allow_html=True)
    if v:
        _submit(v.text, v.files)

    pending = ss.pending if ss.user else None
    conv = store.get_conversation(ss.conv_id) if ss.conv_id else None

    if pending and conv is None:   # 첫 메시지 → 대화 생성 + 공지 메시지
        ss.conv_id = store.create_conversation(ss.user.owner_key, pending["query"][:40], ss.guard_level)
        store.add_message(ss.conv_id, "bot", NOTICE_TEXT, meta={"kind": "notice"})
        conv = store.get_conversation(ss.conv_id)
    if pending:
        label = pending["query"] + "".join(f"\n📎 {a['name']}" for a in pending["attachments"])
        store.add_message(ss.conv_id, "user", label)

    msgs = store.get_messages(ss.conv_id) if conv else []
    date_str = datetime.fromisoformat(conv["created_at"] if conv else store.now()).strftime("%Y/%m/%d")
    parts = [f'<div class="chat-wrap"><div class="date">{date_str}</div>']
    if not msgs:
        parts.append(_bot_html(NOTICE_TEXT, datetime.now().strftime("%H:%M")))
    # 안내 문구(warn)는 가장 마지막 답변 1개에만 표시
    last_answer_i = max((i for i, m in enumerate(msgs)
                         if m["role"] != "user" and m["meta"].get("kind") == "answer"), default=-1)
    for i, m in enumerate(msgs):
        ts = _hhmm(m["created_at"])
        if m["role"] == "user":
            parts.append(_user_html(m["content"], ts))
        else:
            parts.append(_bot_html(m["content"], ts, refs=m["refs"], warn=(i == last_answer_i)))
    st.html("".join(parts) + "</div>")

    if not pending:
        return

    # --- ③ 답변 생성 중 표시 → 파이프라인 실행 ---
    ss.pending = None
    ph = st.empty()
    show = lambda s: ph.html(f'<div class="chat-wrap">{_typing_html(s)}</div>')
    show(pipeline.STATUS_ANALYZE)
    history = [{"role": m["role"], "content": m["content"]} for m in msgs[:-1]
               if m["meta"].get("kind") != "notice"]
    req = pipeline.TurnRequest(user=ss.user, query=pending["query"], history=history,
                               guard_level=ss.guard_level, conv_id=ss.conv_id, session_id=ss.sid,
                               attachments=pending["attachments"])
    try:
        res = pipeline.run_turn(req, on_status=show)
    except NotImplementedError as e:
        res = pipeline.TurnResult(f"⚠️ {e}", kind="error")
    store.add_message(ss.conv_id, "bot", res.text, refs=res.refs,
                      meta={"kind": res.kind, "decision": res.decision, "guard_level": ss.guard_level})
    if res.refs:   # 슈리챗처럼 자료 기반 답변이 나오면 대화 제목이 답변 첫 줄로 바뀜
        store.set_title(ss.conv_id, _title_from_answer(res.text))
    ss.last_debug = res.debug
    st.rerun()


# =============================================================================
# 모달들
# =============================================================================
def _header(title, back_to=None, close_key="x"):
    """뒤로가기(<) + 제목 + 닫기(X) 헤더. X는 Streamlit 기본 닫기 버튼을 그대로 사용."""
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        if back_to:
            if st.button("", icon=":material/chevron_left:", key="back"):
                _open(*back_to)
                st.rerun()
        st.html(f'<div class="dlg-title">{title}</div>')
    st.html('<div class="dlg-hr"></div>')


@st.dialog(" ", width="small")
def dlg_settings(_=None):
    ss = st.session_state
    _header("설정")
    st.html('<div class="sec-label">개인정보</div>')
    if st.button("개인정보 관리", key="flat_privacy", icon=":material/chevron_right:", icon_position="right",
                 width="stretch"):
        _open("privacy")
        st.rerun()
    st.html('<div class="sec-label">앱</div>')
    st.button("언어 · 한국어", key="flat_lang", icon=":material/chevron_right:", icon_position="right",
              width="stretch", disabled=True)
    if ss.get("show_research", True):
        _research_panel()


def _research_panel():
    """연구용 설정 — 원본 슈리챗에는 없는 영역. 실험 편의를 위해 설정 모달 맨 아래에만 둔다."""
    ss = st.session_state
    st.html('<div class="dlg-hr"></div><div class="sec-label">🔬 연구용 설정 (원본 UI에는 없음)</div>')
    u = ss.user
    st.caption(f"현재 사용자: **{u.name} · {u.role_label}** (`{u.owner_key}`)" if u else "현재 사용자: 미식별")
    if llm.MOCK_MODE:
        st.caption(f"LLM: **MOCK_MODE** (비용 0원) · 제공자 설정 {llm.PROVIDER}"
                   + (" — 실제 호출하려면 .env 에 API 키 입력 후 재실행" if not llm.HAS_KEY else " (MOCK_MODE=1)"))
    else:
        use = llm.last_usage
        st.caption(f"LLM: **{llm.PROVIDER} · {llm.MODEL_NAME}** (실제 호출)"
                   + (f" · 마지막 호출 입력 {use['input_tokens']:,} / 출력 {use['output_tokens']:,} 토큰" if use else ""))
    levels = list(pipeline.GUARD_LEVELS)
    fmt = lambda k: f"{k} · {pipeline.GUARD_LEVELS[k]['name']}" + ("" if pipeline.GUARD_LEVELS[k]["impl"] else " (예정)")
    if ss.get("lock_level"):
        st.caption(f"비교환경: **{fmt(ss.guard_level)}** (이 실행 파일에서 고정)")
    else:
        lv = st.selectbox("비교환경", levels, index=levels.index(ss.guard_level), format_func=fmt)
        if lv != ss.guard_level:
            if pipeline.GUARD_LEVELS[lv]["impl"]:
                ss.guard_level = lv
            else:
                st.warning(f"{lv}는 아직 구현되지 않았어요.")
    ps = auth.personas()
    idx = st.selectbox("페르소나 빠른 전환", range(len(ps)), format_func=lambda i: ps[i]["label"])
    if st.button("이 페르소나로 로그인", key="persona_go", width="stretch"):
        _login(ps[idx]["name"], ps[idx]["login_id"])
        _go_home()
        st.rerun()
    if ss.last_debug:
        with st.expander("마지막 응답에 들어간 컨텍스트"):
            st.json({k: v for k, v in ss.last_debug.items() if k != "context"})
            st.code(ss.last_debug.get("context", ""), language=None, wrap_lines=True)


@st.dialog(" ", width="small")
def dlg_privacy(_=None):
    u = st.session_state.user
    consent = store.get_consent(u.owner_key) if u else None
    _header("개인정보 관리", back_to=("settings", None))
    st.html('<div class="dlg-label">개인정보 수집 및 이용 동의</div>')
    if st.button("전문 보기", key="row_full", icon=":material/description:", width="stretch"):
        _open("consent", "privacy")
        st.rerun()
    st.html('<div class="agree-row"><div><b>개인정보 수집 및 이용 동의 (필수 항목)</b>'
            '<p>※ 미동의 시 서비스 이용이 불가합니다.<br>서비스 이용을 위해 반드시 동의가 필요합니다.</p></div>'
            f'<span>{"동의함" if consent else "미동의"}</span></div>')
    st.html('<div style="height:190px"></div><div class="dlg-hr"></div>')
    if st.button("개인정보 삭제 및 이용 종료", key="del", icon=":material/delete:", width="stretch",
                 disabled=u is None):
        _open("withdraw")
        st.rerun()


def _consent_dismiss():
    # 개인 정보 입력 모달에서 들어온 경우, X로 닫아도 입력 모달로 되돌아감
    if st.session_state.get("consent_back") == "info":
        _open("info")


@st.dialog(" ", width="small", on_dismiss=_consent_dismiss)
def dlg_consent(back="privacy"):
    st.session_state.consent_back = back
    if back == "info":   # 개인 정보 입력 → '개인정보 필수 항목 수집 및 이용' 링크로 들어온 경우
        _header("개인정보 수집 및 이용 동의")
    else:                # 설정 → 개인정보 관리 → 전문 보기
        _header("개인정보 수집 및 이용 동의 내역", back_to=(back, None))
    body = "".join(f"<h4>{h}</h4><p>{p}</p>" for h, p in CONSENT_TEXT)
    st.html(f'<div class="consent-body">{body}</div><div style="height:150px"></div><div class="dlg-hr"></div>')
    if st.button("확인", type="primary", width="stretch", key="consent_ok"):
        st.session_state.consent_back = None
        _open(back)
        st.rerun()


@st.dialog(" ", width="small")
def dlg_withdraw(_=None):
    ss = st.session_state
    _header("서비스 이용을 종료할까요?")
    st.html('<div class="dlg-text">지금까지 커넥트에서 나눈 모든 대화 기록과 저장된 데이터가 즉시 삭제되며 '
            '복구가 불가능합니다.</div><div class="dlg-hr"></div>')
    if st.button("계속 이용할게요", type="primary", width="stretch", key="wd_keep"):
        _open("privacy")
        st.rerun()
    if st.button("개인정보 삭제 및 이용 종료", width="stretch", key="wd_go"):
        n = store.withdraw(ss.user.owner_key)
        store.audit(ss.sid, ss.user, ss.guard_level, None, "withdraw", "DELETE", None, {"conversations": n})
        ss.user, ss.conv_id, ss.view = None, None, "home"
        ss.toast = "개인정보가 삭제되고 이용이 종료되었어요."
        st.rerun()


def _login(name, login_id):
    ss = st.session_state
    u = auth.identify(name, login_id)
    if u is None:                          # DB에 없는 사용자 → 로그인 거부(게스트 없음)
        return None
    store.save_consent(u.owner_key, u.name, u.login_id, u.role)
    store.audit(ss.sid, u, ss.guard_level, None, "auth", "IDENTIFIED", None, {"role": u.role})
    ss.user = u
    if ss.pending and ss.view == "home":   # 모달 저장 후 보류된 첫 질문으로 대화 시작
        ss.view, ss.conv_id = "chat", None
    return u


@st.dialog(" ", width="small")
def dlg_info(_=None):
    ss = st.session_state
    draft = ss.pop("info_draft", {})   # 동의 안내 창에 다녀오면 입력값 복원
    _header("개인 정보 입력")
    st.html('<div class="dlg-text">더 정확하고 개인화된 답변을 위해 기본 정보를 입력해 주세요.<br>'
            '입력하신 정보는 상담 목적으로만 사용되며, <b>오프라인 시 문자로 답변 알림을 보내드려요.</b></div>')
    st.html('<div class="field-label">이름<em>*</em></div>')
    name = st.text_input("이름", value=draft.get("name", ""), key="info_name",
                         label_visibility="collapsed", live=True)
    st.html('<div class="field-label">학번 <span style="font-weight:400;color:#8f92a4">(교직원은 사번)</span><em>*</em></div>')
    login_id = st.text_input("학번", value=draft.get("id", ""), key="info_id",
                             label_visibility="collapsed", live=True)
    with st.container(horizontal=True, vertical_alignment="center", gap=None, key="agree_row"):
        agree = st.checkbox("동의", value=draft.get("agree", False), key="info_agree",
                            label_visibility="collapsed")
        st.html('<span class="agree-label"><span class="req-tag">필수</span></span>')
        if st.button("개인정보 필수 항목 수집 및 이용", key="consent_link", type="tertiary"):
            ss.info_draft = {"name": name, "id": login_id, "agree": agree}
            _open("consent", "info")
            st.rerun()
        st.html('<span class="agree-label">에 동의합니다.</span>')
    ok = bool(name and name.strip() and login_id and len(login_id.strip()) >= 4 and agree)
    if st.button("저장", key="info_save", type="primary", width="stretch", disabled=not ok):
        if _login(name, login_id) is None:   # 미등록 사용자: 안내 후 모달 유지
            ss.info_draft = {"name": name, "id": login_id, "agree": agree}
            ss.toast = "등록된 사용자가 아니에요. 이름과 학번(교직원은 사번)을 확인해 주세요."
            _open("info")
        st.rerun()


@st.dialog(" ", width="small")
def dlg_faq(i=0):
    title, body = FAQ[i]
    _header(title)
    st.markdown(body)
    if st.button("확인", type="primary", width="stretch", key="faq_ok"):
        st.rerun()


@st.dialog(" ", width="small")
def dlg_manager(_=None):
    ss = st.session_state
    _header("매니저 문의 남기기")
    if ss.user is None:
        st.html('<div class="dlg-text">문의를 남기려면 먼저 개인 정보를 입력해 주세요.</div>')
        if st.button("개인 정보 입력", type="primary", width="stretch", key="mgr_info"):
            _open("info")
            st.rerun()
        return
    st.html('<div class="dlg-text">담당부서에서 확인 후 답변드려요. 오프라인 시 '
            '학교에 등록된 연락처로 답변 알림을 보내드려요.</div>')
    text = st.text_area("문의 내용", key="mgr_text", label_visibility="collapsed",
                        placeholder="문의 내용을 입력해 주세요", height=140)
    if st.button("문의 남기기", type="primary", width="stretch", key="mgr_send", disabled=not text.strip()):
        store.add_inquiry(ss.user.owner_key, ss.conv_id, text.strip())
        ss.toast = "문의가 접수되었어요."
        st.rerun()


DIALOGS = {"settings": dlg_settings, "privacy": dlg_privacy, "consent": dlg_consent,
           "withdraw": dlg_withdraw, "info": dlg_info, "faq": dlg_faq, "manager": dlg_manager}


# =============================================================================
# 진입점
# =============================================================================
def run_app(default_level="E0", lock_level=False, show_research=True):
    st.set_page_config(page_title="슈리CHAT", page_icon=str(AVATAR_PATH) if AVATAR_PATH.exists() else "🎓",
                       layout="wide", initial_sidebar_state="expanded")
    store.init()
    _init_state(default_level)
    ss = st.session_state
    ss.lock_level = lock_level
    ss.show_research = show_research
    if lock_level:
        ss.guard_level = default_level

    _inject_css(ss.view)
    _sidebar()
    _window_controls()
    if ss.toast:
        st.toast(ss.toast)
        ss.toast = None

    dialog = ss.dialog
    ss.dialog = None
    if dialog:                     # 다이얼로그는 화면 렌더 전에 열어야 답변 생성 중에도 뜸
        name, arg = dialog
        DIALOGS[name](arg) if arg is not None else DIALOGS[name]()

    if ss.view == "home":
        _home()
    else:
        _chat()

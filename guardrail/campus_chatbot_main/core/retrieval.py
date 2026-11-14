"""
공개 학사정보(public_academic_info, D0) 검색 — 외부 라이브러리 없이 한글 2-gram 겹침 점수로 간단히 구현.

슈리챗 화면의 '참고 자료 N개' + 본문 인용번호(1)(2)를 재현하는 용도.
게시판 글(source="board")도 함께 검색되므로 T5(RAG 기반 간접 인젝션) 경로가 자연스럽게 생긴다.
"""
import re
import sqlite3

from core.config import DB_PATH, connect
_STOP = {"알려", "려줘", "뭐야", "언제", "어떻", "하나", "나요", "해줘", "있어", "는지", "인가", "까요"}


def _bigrams(text: str) -> set:
    grams = set()
    for tok in re.findall(r"[가-힣A-Za-z0-9]+", text):
        tok = tok.lower()
        if len(tok) == 1:
            continue
        grams.update(tok[i:i + 2] for i in range(len(tok) - 1))
    return grams - _STOP


def search(query: str, top_k: int = 4, min_score: float = 0.3) -> list[dict]:
    q = _bigrams(query)
    if not q:
        return []
    conn = connect(row=True)
    docs = []
    for r in conn.execute("SELECT * FROM public_academic_info"):
        docs.append({"doc_id": r["doc_id"], "info_id": r["info_id"], "title": r["title"],
                     "content": r["content"], "category": r["category"],
                     "source": r["source_type"], "url": r["source_url"],
                     "trust_level": r["trust_level"]})
    conn.close()
    scored = []
    for d in docs:
        title_hit = len(q & _bigrams(d["title"]))
        body_hit = len(q & _bigrams(d["content"]))
        score = (2 * title_hit + body_hit) / (len(q) * 3)
        if score >= min_score:
            scored.append((score, d))
    scored.sort(key=lambda x: -x[0])
    return [dict(d, score=round(s, 3)) for s, d in scored[:top_k]]

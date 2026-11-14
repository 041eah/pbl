"""
모의 학사상담 챗봇 — 그룹① 기본 챗봇 (E0: 가드레일 없음, 실험 기준선)

UI는 ui/app_ui.py(슈리챗 재현), 응답 로직은 core/pipeline.py의 run_e0()에 있다.
의도된 취약점 목록은 core/pipeline.py 상단 E0 주석 참고.

실행:  streamlit run chatbot_baseline.py
"""
from ui.app_ui import run_app

run_app(default_level="E0", lock_level=True)

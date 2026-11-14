"""
LLM 호출 — Claude / Gemini / OpenAI(GPT) 전환 가능.

.env 파일(프로젝트 루트, .gitignore로 깃허브 제외)에 설정:

  # OpenAI(GPT) 유료로 쓸 때
  LLM_PROVIDER=openai
  OPENAI_API_KEY=sk-...
  # OPENAI_MODEL=gpt-4o-mini   (쓸 모델로 변경 — E0~E4 전부 같은 모델로 고정)

  # Gemini 무료로 개발·테스트할 때 (위 두 줄 대신)
  LLM_PROVIDER=gemini
  GEMINI_API_KEY=AIza...
  # GEMINI_MODEL=gemini-3.5-flash-lite   (AI Studio 무료 한도 페이지에서 쓸 모델 확인 후 변경)

  # Claude로 교체할 때
  LLM_PROVIDER=claude
  ANTHROPIC_API_KEY=sk-ant-...

MOCK_MODE 규칙
  - MOCK_MODE=1 → 무조건 가짜 응답(요금 0원) / MOCK_MODE=0 → 무조건 실제 호출
  - 지정 안 함 → 선택한 제공자의 키가 있으면 실제 호출, 없으면 MOCK
"""
import os
import time

from core.config import ROOT


def _load_dotenv():
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.split("#")[0].strip().strip('"').strip("'"))


_load_dotenv()
print("========== LLM 설정 확인 ==========")
print("LLM_PROVIDER =", os.environ.get("LLM_PROVIDER"))
print("OPENAI_MODEL =", os.environ.get("OPENAI_MODEL"))
print("MOCK_MODE =", os.environ.get("MOCK_MODE"))
print("OPENAI_API_KEY 존재 =", bool(os.environ.get("OPENAI_API_KEY")))
print("====================================")

PROVIDER = os.environ.get("LLM_PROVIDER", "claude").strip().lower()
MODELS = {
    "claude": os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
    "gemini": os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite"),
    "openai": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
}
KEY_ENV = {"claude": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}
if PROVIDER not in MODELS:
    PROVIDER = "claude"
MODEL_NAME = MODELS[PROVIDER]
MAX_TOKENS = 1024

HAS_KEY = bool(os.environ.get(KEY_ENV[PROVIDER], "").strip())
_flag = os.environ.get("MOCK_MODE")
MOCK_MODE = (_flag != "0") if _flag in ("0", "1") else not HAS_KEY

_client = None
last_usage = {}            # 마지막 호출의 토큰 사용량 (연구용 패널에 표시)


def _claude(system, messages):
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    resp = _client.messages.create(model=MODEL_NAME, max_tokens=MAX_TOKENS,
                                   system=system, messages=messages)
    last_usage.update(input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens)
    return "".join(b.text for b in resp.content if b.type == "text")


def _gemini(system, messages):
    global _client
    from google import genai
    from google.genai import types
    if _client is None:
        _client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    contents = [types.Content(role="model" if m["role"] == "assistant" else "user",
                              parts=[types.Part(text=m["content"])]) for m in messages]
    resp = _client.models.generate_content(
        model=MODEL_NAME, contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system, max_output_tokens=MAX_TOKENS,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)))
    u = resp.usage_metadata
    if u:
        last_usage.update(input_tokens=u.prompt_token_count or 0, output_tokens=u.candidates_token_count or 0)
    return resp.text or "[빈 응답 — 안전 필터로 차단되었을 수 있어요]"


def _openai(system, messages):
    global _client
    if _client is None:
        import openai
        _client = openai.OpenAI()
    msgs = [{"role": "system", "content": system}] + [dict(m) for m in messages]
    resp = _client.chat.completions.create(model=MODEL_NAME, max_tokens=MAX_TOKENS, messages=msgs)
    u = resp.usage
    if u:
        last_usage.update(input_tokens=u.prompt_tokens or 0, output_tokens=u.completion_tokens or 0)
    return resp.choices[0].message.content or "[빈 응답]"


_HINTS = {
    "AuthenticationError": "API 키가 올바르지 않아요. .env 의 키를 확인하세요.",
    "PermissionDeniedError": "이 키로는 모델 사용 권한이 없어요.",
    "RateLimitError": "요청이 너무 많아요. 잠시 후 다시 시도하세요.",
    "BadRequestError": "요청 형식 오류이거나 크레딧이 부족할 수 있어요 (Console > Billing 확인).",
    "APIConnectionError": "인터넷 연결 또는 방화벽을 확인하세요.",
}


def _is_network(msg: str) -> bool:
    # 네트워크가 순간적으로 끊길 때: 연결 리셋(10054), 타임아웃, 연결 실패 등
    keys = ("10054", "10053", "10060", "ConnectError", "ConnectionError", "Connection reset",
            "Connection aborted", "RemoteProtocolError", "timed out", "Timeout", "EOF",
            "ServerDisconnected", "강제로 끊", "Max retries")
    return any(k in msg for k in keys)


def _err(e) -> str:
    name, msg = type(e).__name__, str(e)
    hint = _HINTS.get(name, "")
    if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
        hint = "무료 사용 한도(분당/일일)를 넘었어요. 잠시 후 또는 내일(한국시간 오후 4~5시 초기화) 다시 시도하세요."
    elif "API_KEY_INVALID" in msg or "401" in msg:
        hint = _HINTS["AuthenticationError"]
    elif _is_network(msg):
        hint = ("네트워크 연결이 끊겼어요. 3번 재시도했지만 실패했어요.\n"
                "· 다른 인터넷(다른 와이파이/핫스팟)으로 바꿔보세요.\n"
                "· 백신·방화벽의 'HTTPS 검사/SSL 검사'가 켜져 있으면 꺼보세요(가장 흔한 원인).\n"
                "· VPN을 쓰고 있으면 꺼보세요.")
    return f"[API 오류: {name}] {hint}\n\n{msg[:500]}"


def _retryable(msg: str, attempt: int) -> float:
    """재시도할지 + 얼마나 기다릴지(초). 0이면 재시도 안 함."""
    if attempt >= 3:
        return 0
    if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
        return 15                          # 무료 한도 → 15초 후
    if _is_network(msg):
        return 2 * (attempt + 1)           # 네트워크 끊김 → 2, 4초 후
    return 0


def generate(system: str, messages: list[dict]) -> str:
    """messages: [{"role": "user"|"assistant", "content": str}, ...]  (user로 시작)"""
    fn = {"gemini": _gemini, "openai": _openai}.get(PROVIDER, _claude)
    for attempt in range(4):
        try:
            return fn(system, messages)
        except Exception as e:
            wait = _retryable(str(e), attempt)
            if wait:
                time.sleep(wait)
                continue
            return _err(e)


# =============================================================================
# 도구(함수) 호출 지원 — 실제 학사시스템 연동 챗봇처럼 LLM이 DB 조회 함수를 스스로 부른다.
#   tools    : core.academic_api.TOOL_SPECS (제공자 무관 공통 스키마)
#   dispatch : (name, args) -> 결과(dict/list)  ← 실제 DB 조회. 권한 검사는 호출부가 담당.
#   on_call  : (name, args, result) 콜백 — 감사로그·디버그용(선택)
# 반환: (최종답변텍스트, 호출기록리스트)
# =============================================================================
MAX_TOOL_ROUNDS = 8
_NO_ANSWER = "요청하신 내용을 정리하지 못했어요. 질문을 조금 더 구체적으로 해주시겠어요?"


def generate_with_tools(system, messages, tools, dispatch, on_call=None):
    fn = {"gemini": _gemini_tools, "openai": _openai_tools}.get(PROVIDER, _claude_tools)
    cb = on_call or (lambda *a: None)
    for attempt in range(4):
        try:
            return fn(system, messages, tools, dispatch, cb)
        except Exception as e:
            wait = _retryable(str(e), attempt)
            if wait:
                time.sleep(wait)
                continue
            return _err(e), []


def _claude_tools(system, messages, tools, dispatch, on_call):
    global _client
    import anthropic
    if _client is None:
        _client = anthropic.Anthropic()
    schema = [{"name": t["name"], "description": t["description"],
               "input_schema": {"type": "object", "properties": t.get("parameters", {}),
                                "required": t.get("required", [])}} for t in tools]
    msgs = [dict(m) for m in messages]
    calls, seen = [], set()
    for _ in range(MAX_TOOL_ROUNDS):
        resp = _client.messages.create(model=MODEL_NAME, max_tokens=MAX_TOKENS, system=system,
                                       tools=schema, messages=msgs)
        last_usage.update(input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens)
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            return "".join(b.text for b in resp.content if b.type == "text"), calls
        msgs.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})
        results, repeat = [], False
        for tu in tool_uses:
            out = dispatch(tu.name, dict(tu.input))
            on_call(tu.name, dict(tu.input), out)
            calls.append({"tool": tu.name, "args": dict(tu.input), "result": out})
            results.append({"type": "tool_result", "tool_use_id": tu.id, "content": _json(out)})
            sig = f"{tu.name}:{_json(dict(tu.input))}"
            repeat = repeat or sig in seen
            seen.add(sig)
        msgs.append({"role": "user", "content": results})
        if repeat:                       # 같은 도구를 같은 인자로 재호출 → 루프로 보고 최종 답변 유도
            break
    # 한도 도달/반복 감지: 도구 없이 최종 답변을 강제한다(“중단” 대신 실제 답)
    final = _client.messages.create(model=MODEL_NAME, max_tokens=MAX_TOKENS, system=system, messages=msgs)
    return ("".join(b.text for b in final.content if b.type == "text") or _NO_ANSWER), calls


def _gemini_tools(system, messages, tools, dispatch, on_call):
    global _client
    from google import genai
    from google.genai import types
    if _client is None:
        _client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    decls = [types.FunctionDeclaration(
        name=t["name"], description=t["description"],
        parameters={"type": "object", "properties": _gemini_props(t.get("parameters", {})),
                    "required": t.get("required", [])}) for t in tools]
    cfg = types.GenerateContentConfig(
        system_instruction=system, max_output_tokens=MAX_TOKENS,
        tools=[types.Tool(function_declarations=decls)],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    contents = [types.Content(role="model" if m["role"] == "assistant" else "user",
                              parts=[types.Part(text=m["content"])]) for m in messages]
    calls, seen = [], set()
    for _ in range(MAX_TOOL_ROUNDS):
        resp = _client.models.generate_content(model=MODEL_NAME, contents=contents, config=cfg)
        if resp.usage_metadata:
            last_usage.update(input_tokens=resp.usage_metadata.prompt_token_count or 0,
                              output_tokens=resp.usage_metadata.candidates_token_count or 0)
        fcalls = [p.function_call for p in (resp.candidates[0].content.parts or [])
                  if getattr(p, "function_call", None)]
        if not fcalls:
            return (resp.text or "[빈 응답 — 안전 필터로 차단되었을 수 있어요]"), calls
        contents.append(resp.candidates[0].content)
        parts, repeat = [], False
        for fc in fcalls:
            args = dict(fc.args or {})
            out = dispatch(fc.name, args)
            on_call(fc.name, args, out)
            calls.append({"tool": fc.name, "args": args, "result": out})
            parts.append(types.Part.from_function_response(name=fc.name, response={"result": out}))
            sig = f"{fc.name}:{_json(args)}"
            repeat = repeat or sig in seen
            seen.add(sig)
        contents.append(types.Content(role="user", parts=parts))
        if repeat:                       # 같은 도구를 같은 인자로 재호출 → 루프로 보고 최종 답변 유도
            break
    # 한도 도달/반복 감지: 도구 없이 최종 답변을 강제한다(“중단” 대신 실제 답)
    cfg_notool = types.GenerateContentConfig(system_instruction=system, max_output_tokens=MAX_TOKENS)
    final = _client.models.generate_content(model=MODEL_NAME, contents=contents, config=cfg_notool)
    return (final.text or _NO_ANSWER), calls


def _openai_tools(system, messages, tools, dispatch, on_call):
    global _client
    import openai
    if _client is None:
        _client = openai.OpenAI()
    schema = [{"type": "function", "function": {
        "name": t["name"], "description": t["description"],
        "parameters": {"type": "object", "properties": t.get("parameters", {}),
                       "required": t.get("required", [])}}} for t in tools]
    msgs = [{"role": "system", "content": system}] + [dict(m) for m in messages]
    calls, seen = [], set()
    for _ in range(MAX_TOOL_ROUNDS):
        resp = _client.chat.completions.create(model=MODEL_NAME, max_tokens=MAX_TOKENS,
                                               messages=msgs, tools=schema)
        if resp.usage:
            last_usage.update(input_tokens=resp.usage.prompt_tokens or 0,
                              output_tokens=resp.usage.completion_tokens or 0)
        msg = resp.choices[0].message
        tcalls = msg.tool_calls or []
        if not tcalls:
            return (msg.content or ""), calls
        msgs.append(msg.model_dump(exclude_none=True))
        repeat = False
        for tc in tcalls:
            import json as _j
            try:
                args = _j.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            out = dispatch(tc.function.name, args)
            on_call(tc.function.name, args, out)
            calls.append({"tool": tc.function.name, "args": args, "result": out})
            msgs.append({"role": "tool", "tool_call_id": tc.id, "content": _json(out)})
            sig = f"{tc.function.name}:{_json(args)}"
            repeat = repeat or sig in seen
            seen.add(sig)
        if repeat:                       # 같은 도구를 같은 인자로 재호출 → 루프로 보고 최종 답변 유도
            break
    # 한도 도달/반복 감지: 도구 없이 최종 답변을 강제한다(“중단” 대신 실제 답)
    final = _client.chat.completions.create(model=MODEL_NAME, max_tokens=MAX_TOKENS, messages=msgs)
    return (final.choices[0].message.content or _NO_ANSWER), calls


def _gemini_props(props):
    # 공통 스키마 → Gemini 형식(type 대문자). enum/description 유지.
    tmap = {"string": "STRING", "integer": "INTEGER", "number": "NUMBER", "boolean": "BOOLEAN"}
    out = {}
    for k, v in props.items():
        p = {"type": tmap.get(v.get("type", "string"), "STRING")}
        if "description" in v:
            p["description"] = v["description"]
        if "enum" in v:
            p["enum"] = v["enum"]
        out[k] = p
    return out


def _json(o):
    import json
    return json.dumps(o, ensure_ascii=False, default=str)
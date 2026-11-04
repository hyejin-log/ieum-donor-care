"""
이음 안내 Agent
공여자의 질문에 답하는 LLM Agent. Claude API의 도구 사용(tool use)으로
공여자 본인의 기록을 조회하고, 안내문을 찾아 답한다.

- LLM은 두 가지 중 하나를 쓴다 (.env 의 LLM_PROVIDER)
    ollama    : 내 컴퓨터에서 도는 무료 모델 (기본값). 기록이 외부로 나가지 않는다.
    anthropic : Claude API (유료, ANTHROPIC_API_KEY 필요)
- LLM에 연결할 수 없으면 '데모 모드'로 간단한 규칙 답변을 한다.
- 응급 증상 단어가 들어오면 LLM을 부르지 않고 바로 응급 안내를 한다.
- 도구는 서버가 공여자 코드를 고정해서 실행하므로, 다른 공여자의 기록은 볼 수 없다.
"""
import json
import os
import urllib.error
import urllib.request

try:
    import anthropic
except ImportError:          # 패키지가 없으면 데모 모드로만 동작
    anthropic = None

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")          # ollama | anthropic | demo
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
OLLAMA_TIMEOUT = 120                                         # 내 컴퓨터(CPU)에서는 답이 느릴 수 있다
MAX_TOOL_ROUNDS = 5          # 도구 호출 → 답변 반복의 최대 횟수
MAX_TOKENS = 700

# ---------- 1) 안전장치: 응급 증상 ----------

EMERGENCY_WORDS = [
    "가슴 통증", "가슴이 아파", "숨이 차", "숨쉬기", "호흡곤란", "의식", "쓰러",
    "소변이 안 나", "소변이 안나", "피가 섞인 소변", "혈뇨", "심한 두통", "마비",
]
EMERGENCY_REPLY = (
    "말씀하신 증상은 바로 진료가 필요할 수 있어요. 지금 119에 연락하거나 가까운 응급실로 가 주세요. "
    "이동이 가능하다면 이식센터에도 알려 주세요. 이 안내 창은 응급 상황에 답할 수 없습니다."
)


def is_emergency(text: str) -> bool:
    t = text.replace(" ", "")
    return any(w.replace(" ", "") in t for w in EMERGENCY_WORDS)


# ---------- 2) 안내문 (지식 베이스) ----------
# 데모용 안내문이다. 실제 운영 전에는 이식센터 의료진의 검토가 필요하다.

GUIDE = {
    "추적검사": "생체 신장 기증 후에는 기증 후 6개월, 1년, 2년에 정기 추적검사를 받습니다. 이음에서는 예정일 앞뒤 60일 안에 받으면 기한 안에 받은 것으로 봅니다. 검사는 혈액(크레아티닌, eGFR)과 소변(단백) 검사, 혈압·체중 측정으로 이루어집니다.",
    "혈압 측정": "아침에 일어나 화장실을 다녀온 뒤, 식사와 약 복용 전에 재는 것이 좋습니다. 등을 기대고 앉아 1~2분 쉬고, 팔은 심장 높이에 둡니다. 두 번 재서 평균을 적으면 더 정확합니다. 커피·담배·운동 직후 30분은 피하세요.",
    "기록 방법": "이음 첫 화면의 '이번 주 기록' 카드에서 '기록하기'를 눌러 혈압(필수)과 체중(선택)을 입력합니다. 주 1회 기록을 권장하며, 같은 날 다시 입력하면 새 값으로 바뀝니다.",
    "eGFR": "eGFR은 콩팥이 1분 동안 혈액을 거르는 능력을 나타내는 추정값입니다. 신장을 하나 기증하면 남은 신장이 일을 나누어 맡기 때문에, 기증 1년 무렵에는 보통 기증 전의 60~70% 정도가 됩니다. 수치의 의미는 담당 의료진이 개인 상황에 맞게 설명해 드립니다.",
    "진통제": "이부프로펜, 나프록센 같은 소염진통제(NSAIDs)는 콩팥에 부담을 줄 수 있어, 기증 후에는 복용 전에 의료진과 상의하는 것이 좋습니다. 일반적으로 아세트아미노펜(타이레놀 계열)이 먼저 권해지지만, 복용 여부와 양은 의료진·약사와 확인하세요.",
    "생활 습관": "물을 충분히 마시고, 짜게 먹지 않으며, 규칙적으로 운동하고, 금연하는 것이 남은 신장을 지키는 데 도움이 됩니다. 체중이 늘지 않도록 관리하는 것도 중요합니다. 단백질 보충제나 한약은 복용 전에 의료진과 상의하세요.",
    "응급 증상": "가슴 통증, 숨이 참, 소변이 거의 나오지 않음, 소변에 피가 섞임, 갑작스러운 심한 두통이나 마비가 있으면 바로 119 또는 응급실로 가세요.",
    "연락처": "검사 일정 변경이나 궁금한 점은 이식센터 코디네이터에게 연락하세요. (데모: 실제 연락처는 병원에서 안내합니다.)",
}


def search_guide(topic: str) -> dict:
    """주제 단어로 안내문을 찾는다. 정확히 없으면 단어가 겹치는 항목을 돌려준다."""
    if topic in GUIDE:
        return {"topic": topic, "text": GUIDE[topic]}
    hits = {k: v for k, v in GUIDE.items() if k in topic or topic in k or any(w in v for w in topic.split())}
    return {"topic": topic, "results": hits or {}, "available_topics": list(GUIDE)}


# ---------- 3) 도구 정의 (Claude에게 보여주는 설명서) ----------

TOOLS = [
    {"name": "get_donation_summary",
     "description": "이 공여자의 기증일, 기증 후 일수, 기증 전 eGFR, 최근 eGFR과 기증 전 대비 %, 추적검사 일정과 상태(done 완료, window 검사 기간, missed 놓침, upcoming 예정)를 조회한다.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_home_records",
     "description": "이 공여자가 직접 기록한 최근 12주의 혈압(수축기/이완기, mmHg)과 체중(kg)을 날짜순으로 조회한다.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_lab_results",
     "description": "병원 검사 결과(검사일, 크레아티닌, eGFR, 소변 단백, 연결된 추적검사)를 날짜순으로 조회한다.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_notifications",
     "description": "지금 이 공여자에게 표시되는 알림(검사 기간, 놓친 검사, 이번 주 기록 요청, 혈압 주의 등)을 조회한다.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "search_guide",
     "description": "이식센터 안내문을 주제로 찾는다. 주제: " + ", ".join(GUIDE) + ".",
     "input_schema": {"type": "object",
                      "properties": {"topic": {"type": "string", "description": "찾을 주제"}},
                      "required": ["topic"]}},
]

SYSTEM_PROMPT = """당신은 생체 신장 기증자 추적관리 앱 '이음'의 안내 도우미입니다.
대화 상대는 신장을 기증한 공여자 본인입니다.

규칙:
1. 공여자의 기록, 일정, 검사 값은 반드시 도구로 조회한 내용만 말하고, 날짜를 함께 알려 주세요. 추측하지 마세요.
2. 일반 안내는 search_guide 도구의 안내문을 바탕으로 답하세요. 안내문에 없는 의학 정보는 지어내지 마세요.
3. 진단, 처방, 약 용량 결정, 검사 수치가 정상인지 비정상인지의 판단은 하지 마세요. 그런 질문에는 확인된 값과 일반적인 설명만 전하고 "담당 의료진에게 확인해 주세요"라고 안내하세요.
4. 응급 증상(가슴 통증, 숨이 참, 소변이 거의 나오지 않음, 혈뇨, 심한 두통, 마비)이 언급되면 다른 설명보다 먼저 119나 응급실을 안내하세요.
5. 이음 앱과 기증 후 관리에 관한 질문이 아니면 정중히 범위를 벗어난다고 말하세요.
6. 한국어 존댓말로, 3~6문장 정도로 짧고 따뜻하게 답하세요. 목록이 필요할 때만 짧은 글머리표를 쓰세요."""

DISCLAIMER = "이 안내는 참고용이며 진료를 대신하지 않아요."


# ---------- 4) Agent 실행 ----------

def call_tool(name: str, args: dict, impl: dict):
    """도구 이름에 맞는 함수를 실행한다. impl은 서버(main.py)가 넘겨준 조회 함수 모음."""
    if name == "search_guide":
        return search_guide(args.get("topic", ""))
    if name in impl:
        return impl[name]()
    return {"error": f"알 수 없는 도구: {name}"}


def run_agent(messages: list[dict], impl: dict) -> dict:
    """
    messages: [{"role": "user"|"assistant", "content": "..."}] 대화 기록 (마지막은 user)
    impl: {"get_donation_summary": 함수, ...} 이 공여자로 고정된 조회 함수
    돌려주는 값: {"reply", "tools_used", "mode"}
    """
    question = messages[-1]["content"]

    # 안전장치가 LLM보다 먼저
    if is_emergency(question):
        return {"reply": EMERGENCY_REPLY, "tools_used": [], "mode": "safety"}

    if LLM_PROVIDER == "ollama":
        return run_ollama(messages, impl)
    if LLM_PROVIDER != "anthropic" or anthropic is None or not os.getenv("ANTHROPIC_API_KEY"):
        return demo_answer(question, impl)

    client = anthropic.Anthropic()
    convo = [{"role": m["role"], "content": m["content"]} for m in messages]
    used = []
    try:
        for _ in range(MAX_TOOL_ROUNDS):
            resp = client.messages.create(model=MODEL, max_tokens=MAX_TOKENS,
                                          system=SYSTEM_PROMPT, tools=TOOLS, messages=convo)
            if resp.stop_reason != "tool_use":
                text = "".join(b.text for b in resp.content if b.type == "text").strip()
                return {"reply": text or "답변을 만들지 못했어요. 다시 질문해 주세요.",
                        "tools_used": used, "mode": "llm"}

            # Claude가 도구를 요청함 → 실행해서 결과를 돌려준다
            convo.append({"role": "assistant", "content": resp.content})
            results = []
            for block in resp.content:
                if block.type == "tool_use":
                    used.append(block.name)
                    try:
                        out = call_tool(block.name, block.input, impl)
                    except Exception as e:                     # 도구 오류도 Claude에게 알려 준다
                        out = {"error": str(e)}
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": json.dumps(out, ensure_ascii=False, default=str)})
            convo.append({"role": "user", "content": results})
        return {"reply": "질문이 복잡해 답을 정리하지 못했어요. 조금 나누어 질문해 주세요.",
                "tools_used": used, "mode": "llm"}
    except anthropic.APIError as e:
        fallback = demo_answer(question, impl)
        fallback["reply"] = f"(AI 연결 오류: {type(e).__name__}) " + fallback["reply"]
        return fallback


# ---------- 4-2) Ollama: 내 컴퓨터의 무료 모델 ----------

def ollama_chat(payload: dict) -> dict:
    """Ollama의 /api/chat 에 요청한다 (표준 라이브러리 urllib 사용)."""
    req = urllib.request.Request(f"{OLLAMA_URL}/api/chat",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as res:
        return json.loads(res.read().decode("utf-8"))




# 작은 로컬 모델은 도구 호출을 자주 빼먹고 값을 지어낸다(실제 확인함).
# 그래서 Ollama에서는 서버가 필요한 기록을 먼저 모아 '자료'로 넣어 주고, 모델은 문장만 만들게 한다.
LOCAL_RULES = """
[답변 규칙]
- 아래 [자료]에 있는 값과 날짜만 사용하세요. [자료]에 없는 숫자는 절대 만들지 마세요.
- 수치가 정상인지, 괜찮은지 판단하지 마세요. "정상", "괜찮아요", "문제없어요" 같은 말을 쓰지 마세요.
- [자료]의 알림에 혈압 주의가 있으면 그 내용을 꼭 전하세요.
- 앱과 기증 후 관리에 관한 질문이 아니면 "죄송해요, 이음 안내는 기증 후 관리와 앱 사용에 대해서만 답할 수 있어요."라고만 답하세요.
- 3~5문장, 한국어 존댓말."""

BANNED = ["정상 범위", "정상입니다", "정상이에요", "괜찮아 보", "문제없", "문제 없", "건강하십니다"]


# 안내문 질문은 모델을 거치지 않고 안내문을 그대로 보여 준다 (작은 모델이 잘못 거절하거나 바꿔 말하는 것을 막음)
GUIDE_KEYWORDS = {
    "진통제": ["진통제", "타이레놀", "이부프로펜", "소염제", "약 먹", "약먹", "두통약"],
    "혈압 측정": ["어떻게 재", "재는 법", "재는법", "측정 방법", "측정법", "언제 재"],
    "생활 습관": ["운동", "음식", "식단", "술", "담배", "영양제", "보충제"],
    "eGFR": ["egfr", "신장 기능", "콩팥 기능", "신기능"],
    "기록 방법": ["기록 방법", "어떻게 기록", "입력 방법"],
    "연락처": ["연락처", "전화번호", "어디로 연락"],
}


def guide_direct(question: str) -> str | None:
    q = question.lower()
    for topic, words in GUIDE_KEYWORDS.items():
        if any(w in q for w in words):
            return topic
    return None


def build_context(question: str, impl: dict) -> tuple[dict, list[str]]:
    """질문과 관계없이 핵심 기록을 모은다. 공여자 1명의 자료라 양이 작다."""
    used = ["get_donation_summary", "get_home_records", "get_lab_results", "get_notifications"]
    s = impl["get_donation_summary"]()
    home = impl["get_home_records"]()
    labs = impl["get_lab_results"]()
    nxt = next((v for v in s["visits"] if v["status"] in ("missed", "window", "upcoming")), None)
    if nxt is None:
        next_text = "예정된 추적검사 없음"
    else:
        state = {"missed": "아직 받지 않음(기간 지남)", "window": "지금 검사 기간", "upcoming": "예정"}[nxt["status"]]
        next_text = f"{nxt['visit_name']} · 예정일 {nxt['due_date']} · {state}"
    ctx = {
        "다음에 받아야 할 추적검사": next_text,
        "오늘 기준 기증 후 일수": s["days_since_donation"],
        "기증일": s["donation_date"],
        "추적검사 일정": [{"검사": v["visit_name"], "예정일": v["due_date"], "완료일": v["completed_date"],
                     "상태": {"done": "완료", "missed": "놓침", "window": "지금 검사 기간",
                            "upcoming": "예정"}[v["status"]]} for v in s["visits"]],
        "최근 자가 기록(최대 4개)": [{"날짜": r["measured_at"], "혈압": f"{r['systolic_bp']}/{r['diastolic_bp']}",
                                "체중": r["weight_kg"]} for r in home[-4:]],
        "최근 병원 검사": ({"검사일": labs[-1]["tested_at"], "eGFR": labs[-1]["egfr"],
                       "기증 전 대비 %": s["egfr_percent_of_predonation"],
                       "소변 단백": labs[-1]["urine_protein"]} if labs else "없음"),
        "지금 알림": [n["title"] for n in impl["get_notifications"]()],
    }
    hits = {k: v for k, v in GUIDE.items() if k.replace(" ", "") in question.replace(" ", "")}
    if hits:                                             # 질문에 주제어가 있을 때만 넣어 자료를 짧게 유지
        ctx["안내문"] = hits; used.append("search_guide")
    return ctx, used


def warm_up() -> bool:
    """모델을 미리 메모리에 올려 첫 답변을 빠르게 한다. 대화 창을 열 때 호출."""
    if LLM_PROVIDER != "ollama":
        return False
    try:
        ollama_chat({"model": OLLAMA_MODEL, "messages": [], "stream": False, "keep_alive": "30m"})
        return True
    except Exception:
        return False


def run_ollama(messages: list[dict], impl: dict) -> dict:
    question = messages[-1]["content"]
    topic = guide_direct(question)
    if topic:
        return {"reply": GUIDE[topic], "tools_used": ["search_guide"], "mode": "guide"}
    rec = record_answer(question, impl)
    if rec:
        return rec
    ctx, used = build_context(question, impl)
    system = SYSTEM_PROMPT + "\n" + LOCAL_RULES + "\n\n[자료]\n" + json.dumps(ctx, ensure_ascii=False, default=str, separators=(",", ":"))
    convo = [{"role": "system", "content": system}] + \
            [{"role": m["role"], "content": m["content"]} for m in messages[-6:]]
    try:
        resp = ollama_chat({"model": OLLAMA_MODEL, "messages": convo, "stream": False,
                            "keep_alive": "30m",                       # 모델을 30분간 메모리에 유지
                            "options": {"temperature": 0.1, "num_predict": 220, "num_ctx": 2048}})
        text = (resp.get("message", {}).get("content") or "").strip()
    except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
        fallback = demo_answer(question, impl)
        fallback["reply"] = "(AI 모델에 연결하지 못해 데모 답변을 보여 드려요) " + fallback["reply"]
        return fallback

    # 출력 검사: 비어 있거나, 수치를 정상이라고 판단하면 규칙 답변으로 바꾼다
    if not text or any(b in text for b in BANNED):
        fallback = demo_answer(question, impl)
        fallback["mode"] = "guarded"
        return fallback
    return {"reply": text, "tools_used": used, "mode": "llm"}


# ---------- 5) 데모 모드: API 키가 없을 때의 간단한 규칙 답변 ----------

def record_answer(question: str, impl: dict) -> dict | None:
    """
    자주 묻는 '내 기록' 질문은 정해진 문장에 실제 값을 넣어 바로 답한다 (0.1초, 지어낼 위험 없음).
    해당하지 않으면 None → AI가 답한다.
    """
    q = question.replace(" ", "").lower()
    if any(w in q for w in ["다음검사", "검사언제", "언제검사", "추적검사", "검사일정", "병원언제", "예약"]):
        s = impl["get_donation_summary"]()
        nxt = next((v for v in s["visits"] if v["status"] in ("missed", "window", "upcoming")), None)
        if nxt is None:
            reply = "지금 예정된 추적검사가 없어요."
        elif nxt["status"] == "missed":
            reply = (f"{nxt['visit_name']} 검사(예정일 {nxt['due_date']})를 아직 받지 않았어요. "
                     "검사 기간이 지났으니 이식센터에 연락해 일정을 잡아 주세요.")
        elif nxt["status"] == "window":
            reply = (f"지금은 {nxt['visit_name']} 검사 기간이에요(예정일 {nxt['due_date']}). "
                     "기간 안에 검사를 받을 수 있도록 이식센터와 일정을 확인해 주세요.")
        else:
            reply = f"다음 검사는 {nxt['visit_name']}이고 예정일은 {nxt['due_date']}이에요. 미리 예약해 두세요."
        return {"reply": reply, "tools_used": ["get_donation_summary"], "mode": "record"}

    if "혈압" in q and not any(w in q for w in ["어떻게", "재는", "측정법", "방법", "언제재"]):
        rows = [r for r in impl["get_home_records"]() if r["systolic_bp"] is not None]
        if not rows:
            return {"reply": "아직 혈압 기록이 없어요. 첫 화면의 '기록하기'로 이번 주 혈압을 남겨 주세요.",
                    "tools_used": ["get_home_records"], "mode": "record"}
        last = rows[-1]
        reply = f"가장 최근 혈압은 {last['measured_at']}에 기록한 {last['systolic_bp']}/{last['diastolic_bp']} mmHg예요."
        if any("혈압" in n["title"] and "높게" in n["title"] for n in impl["get_notifications"]()):
            reply += " 최근 혈압이 높게 측정되고 있다는 알림이 있어요. 같은 시간에 다시 재 보고, 계속 높으면 이식센터에 알려 주세요."
        else:
            reply += " 수치에 대한 판단은 담당 의료진에게 확인해 주세요."
        return {"reply": reply, "tools_used": ["get_home_records", "get_notifications"], "mode": "record"}

    if any(w in q for w in ["검사결과", "결과나왔", "내egfr", "egfr수치", "크레아티닌", "단백뇨", "소변검사"]):
        labs = impl["get_lab_results"]()
        if not labs:
            return {"reply": "아직 등록된 병원 검사 결과가 없어요.", "tools_used": ["get_lab_results"], "mode": "record"}
        last, s = labs[-1], impl["get_donation_summary"]()
        pct = s["egfr_percent_of_predonation"]
        reply = (f"가장 최근 검사({last['tested_at']}) 결과는 eGFR {last['egfr']}"
                 + (f"(기증 전의 {pct}%)" if pct is not None else "")
                 + f", 크레아티닌 {last['creatinine']}, 소변 단백 "
                 + {"Negative": "음성", "Trace": "미량"}.get(last["urine_protein"], last["urine_protein"] or "기록 없음")
                 + "이에요. "
                 "결과의 의미는 담당 의료진이 설명해 드려요.")
        return {"reply": reply, "tools_used": ["get_lab_results", "get_donation_summary"], "mode": "record"}

    if any(w in q for w in ["이번주기록", "기록했", "기록안", "언제기록", "마지막기록"]):
        rows = impl["get_home_records"]()
        reply = (f"마지막 기록은 {rows[-1]['measured_at']}이에요. 주 1회 혈압을 기록해 주세요."
                 if rows else "아직 기록이 없어요. 첫 화면의 '기록하기'로 시작해 보세요.")
        return {"reply": reply, "tools_used": ["get_home_records"], "mode": "record"}
    return None


def demo_answer(question: str, impl: dict) -> dict:
    rec = record_answer(question, impl)
    if rec:
        return rec
    q = question.replace(" ", "")
    if any(w in q for w in ["다음검사", "검사언제", "추적검사", "일정"]):
        s = impl["get_donation_summary"]()
        nxt = next((v for v in s["visits"] if v["status"] in ("window", "upcoming", "missed")), None)
        if nxt is None:
            reply = "예정된 추적검사가 없어요."
        else:
            state = {"window": "지금 검사 기간이에요", "missed": "아직 받지 않은 검사예요",
                     "upcoming": "예정된 검사예요"}[nxt["status"]]
            reply = f"{nxt['visit_name']} 검사(예정일 {nxt['due_date']})가 {state}. 이식센터에 연락해 일정을 확인해 주세요."
        return {"reply": reply, "tools_used": ["get_donation_summary"], "mode": "demo"}
    if "혈압" in q and any(w in q for w in ["어떻게", "재는", "측정법", "방법"]):
        return {"reply": GUIDE["혈압 측정"], "tools_used": ["search_guide"], "mode": "demo"}
    if "혈압" in q:
        rows = [r for r in impl["get_home_records"]() if r["systolic_bp"] is not None]
        if not rows:
            reply = "아직 혈압 기록이 없어요. 첫 화면의 '기록하기'로 이번 주 혈압을 남겨 주세요."
        else:
            last = rows[-1]
            reply = (f"가장 최근 기록은 {last['measured_at']}의 {last['systolic_bp']}/{last['diastolic_bp']} mmHg예요. "
                     "값이 계속 높게 나오면 이식센터에 알려 주세요.")
        return {"reply": reply, "tools_used": ["get_home_records"], "mode": "demo"}
    for topic in ["진통제", "eGFR", "생활 습관", "기록 방법", "연락처"]:
        if topic.replace(" ", "") in q or (topic == "진통제" and "약" in q) or (topic == "생활 습관" and any(w in q for w in ["운동", "음식", "물"])):
            return {"reply": GUIDE[topic], "tools_used": ["search_guide"], "mode": "demo"}
    return {"reply": "데모 모드에서는 '다음 검사', '혈압', '혈압 재는 법', '진통제', 'eGFR', '생활 습관'에 대해 답할 수 있어요. "
                     "AI 연결(API 키)을 설정하면 자유롭게 질문할 수 있어요.",
            "tools_used": [], "mode": "demo"}

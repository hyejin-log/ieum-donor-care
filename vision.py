"""
혈압계 사진 읽기: 사진 속 혈압계 화면의 숫자(수축기 · 이완기 · 맥박)를 AI 이미지 모델로 읽는다.

- 읽은 값은 '입력 칸을 채워 주는' 데까지만 쓴다. 저장은 사람이 화면과 비교해 확인한 뒤 직접 누른다.
  (혈압계 화면의 7칸 숫자는 반사·각도 때문에 잘못 읽힐 수 있다)
- 사진은 저장하지 않는다. 숫자만 읽고 버린다.
- Groq 의 이미지 이해 모델을 쓴다 (GROQ_VISION_MODEL 로 바꿀 수 있음).
"""
import json
import os
import re
import urllib.error
import urllib.request

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# 이미지를 이해하는 모델 후보 (Groq 가 제공하는 모델은 자주 바뀐다 → 이 키로 쓸 수 있는 것 중 앞에서부터 시도)
VISION_PREFERRED = ["meta-llama/llama-4-scout-17b-16e-instruct", "meta-llama/llama-4-maverick-17b-128e-instruct"]
VISION_HINTS = ("llama-4", "vision", "-vl", "qwen")
_working_model: str | None = None

PROMPT = (
    "This is a photo of a home blood pressure monitor display. Read the numbers on the display.\n"
    "SYS (systolic) is the top/largest number, DIA (diastolic) is the middle number, PUL (pulse) is usually the bottom number.\n"
    "Return ONLY a JSON object like {\"systolic\": 120, \"diastolic\": 80, \"pulse\": 70, \"readable\": true}.\n"
    "If the photo is not a blood pressure monitor or the numbers are unclear, return "
    "{\"systolic\": null, \"diastolic\": null, \"pulse\": null, \"readable\": false}. Do not guess."
)


class VisionError(Exception):
    pass


def parse_reply(text: str) -> dict:
    """모델 답에서 JSON을 꺼내 값이 말이 되는지 확인한다. 이상하면 읽지 못한 것으로 본다. (순수 함수)"""
    d = {}
    for m in reversed(re.findall(r"\{[^{}]*\}", text or "")):      # 생각 과정이 섞여도 마지막 JSON을 쓴다
        try:
            d = json.loads(m)
            break
        except json.JSONDecodeError:
            continue

    def as_int(v):
        try:
            return int(round(float(v)))
        except (TypeError, ValueError):
            return None

    sys_, dia, pul = as_int(d.get("systolic")), as_int(d.get("diastolic")), as_int(d.get("pulse"))
    ok = (sys_ is not None and dia is not None and 60 <= sys_ <= 260 and 30 <= dia <= 160 and sys_ > dia)
    if not ok:
        return {"systolic": None, "diastolic": None, "pulse": None, "readable": False}
    if pul is not None and not 30 <= pul <= 220:
        pul = None
    return {"systolic": sys_, "diastolic": dia, "pulse": pul, "readable": True}


def candidate_models(available: list[str]) -> list[str]:
    """쓸 수 있는 모델 중 이미지 모델 후보를 순서대로 (순수 함수)"""
    env = os.getenv("GROQ_VISION_MODEL")
    out = [env] if env else []
    out += [m for m in VISION_PREFERRED if m in available]
    out += [m for m in available if any(h in m for h in VISION_HINTS) and m not in out]
    return out


def _ask(model: str, key: str, data_url: str) -> str:
    body = {"model": model, "temperature": 0, "max_completion_tokens": 600,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]}]}
    if model.startswith("qwen/"):
        body["reasoning_format"] = "hidden"               # 생각 과정은 빼고 답만
    req = urllib.request.Request(GROQ_URL, data=json.dumps(body).encode("utf-8"), headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}", "User-Agent": "ieum-donor-care/1.0"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read())["choices"][0]["message"]["content"] or ""


def read_monitor(data_url: str) -> dict:
    global _working_model
    key = (os.getenv("GROQ_API_KEY") or "").strip().strip('"\'').strip()
    if not key:
        raise VisionError("사진 읽기 기능이 설정되지 않았어요 (AI 키 없음)")
    if _working_model:
        models = [_working_model]
    else:
        import agent                                       # 모델 목록 조회는 agent 의 것을 쓴다
        try:
            models = candidate_models(agent.groq_models())
        except Exception:
            models = VISION_PREFERRED[:1]
    if not models:
        raise VisionError("지금은 사진을 읽을 수 있는 AI 모델이 없어요")
    last = None
    for model in models:
        try:
            reply = _ask(model, key, data_url)
            _working_model = model
            return parse_reply(reply)
        except urllib.error.HTTPError as e:                # 404: 모델 없음, 400: 이미지를 못 받는 모델 → 다음 후보
            msg = e.read().decode("utf-8", "replace")
            try:
                msg = json.loads(msg)["error"]["message"]
            except Exception:
                pass
            last = f"{e.code}: {msg[:80]}"
            print("[vision]", model, e.code, msg[:200])
            if _working_model == model:
                _working_model = None
            if e.code not in (400, 404):
                break
        except (urllib.error.URLError, TimeoutError):
            raise VisionError("사진 읽기 서버에 연결하지 못했어요")
    raise VisionError(f"사진을 읽지 못했어요 (AI 오류 {last})")

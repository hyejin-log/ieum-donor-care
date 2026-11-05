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
VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

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
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        d = {}

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


def read_monitor(data_url: str) -> dict:
    key = (os.getenv("GROQ_API_KEY") or "").strip().strip('"\'').strip()
    if not key:
        raise VisionError("사진 읽기 기능이 설정되지 않았어요 (AI 키 없음)")
    body = {"model": VISION_MODEL, "temperature": 0, "max_completion_tokens": 120,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]}]}
    req = urllib.request.Request(GROQ_URL, data=json.dumps(body).encode("utf-8"), headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}", "User-Agent": "ieum-donor-care/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            reply = json.loads(res.read())["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        print("[vision]", e.code, detail)
        raise VisionError(f"사진을 읽지 못했어요 (AI 오류 {e.code})")
    except (urllib.error.URLError, TimeoutError):
        raise VisionError("사진 읽기 서버에 연결하지 못했어요")
    return parse_reply(reply)

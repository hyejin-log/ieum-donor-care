"""
EMR 연동: 병원 EMR의 검사 결과를 FHIR(국제 의료정보 표준) 형식으로 받아 이음에 자동 저장한다.

실제 병원 EMR은 병원의 허가·보안 심사·계정(SMART on FHIR 인증)이 있어야 연결할 수 있다.
그래서 포트폴리오에서는 '모의 EMR'을 함께 둔다.
  - EMR_FHIR_BASE 가 비어 있으면: 이 파일 안의 모의 EMR 자료를 바로 읽는다.
  - EMR_FHIR_BASE=http://127.0.0.1:8000/mock-emr/fhir 처럼 주소를 넣으면: HTTP로 FHIR 서버에 요청한다.
    (실제 병원 FHIR 서버 주소로 바꾸면 같은 코드가 그대로 동작하도록 만든 구조)

FHIR 검사 결과(Observation)는 LOINC 코드로 어떤 검사인지 구분한다.
"""
import json
import os
import urllib.parse
import urllib.request
from datetime import date, timedelta

# 이음이 받는 검사 3가지와 LOINC 코드
LOINC = {
    "2160-0": "creatinine",      # Creatinine [Mass/volume] in Serum or Plasma
    "98979-8": "egfr",           # eGFR (CKD-EPI 2021)
    "62238-1": "egfr",           # eGFR (CKD-EPI, 이전 코드)
    "20454-5": "urine_protein",  # Protein [Presence] in Urine by Test strip
}
UP_TEXT = {"negative": "Negative", "음성": "Negative", "trace": "Trace", "미량": "Trace",
           "1+": "1+", "2+": "2+", "3+": "3+"}
DONOR_ID_SYSTEM = "urn:ieum:donor-code"


# ---------- 모의 EMR 자료 (모두 가상 값) ----------
# 날짜는 기증일 기준 (개월, 일)로 적는다: 데모 데이터를 언제 다시 만들어도 같은 상황이 되게.
# 일부는 이음에 이미 있는 결과(중복 → 건너뜀), 일부는 아직 입력되지 않은 새 결과다.
# D-002 크레아티닌은 일부러 µmol/L 단위로 넣어 단위 변환을 확인한다.
MOCK_RESULTS = {
    "D-001": [((0, 7), 1.18, "mg/dL", 74, "Negative"),
              ((12, 23), 1.05, "mg/dL", 84, "Negative")],         # 1년 검사: 이음에 아직 없음
    "D-002": [((0, 7), 1.31, "mg/dL", 66, "Negative"),
              ((6, 10), 107.9, "umol/L", 72, "Trace")],           # 6개월 검사: 이음에 아직 없음
    "D-003": [((0, 7), 1.12, "mg/dL", 58, "Negative")],
}


def add_months(d: date, months: int) -> date:
    """d 에서 months 개월 뒤 (말일은 그 달 마지막 날로 맞춤)"""
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue


def _obs(code: str, i: int, loinc: str, display: str, when: str, value: dict) -> dict:
    return {"resourceType": "Observation", "id": f"{code}-{when}-{i}", "status": "final",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                      "code": "laboratory"}]}],
            "code": {"coding": [{"system": "http://loinc.org", "code": loinc, "display": display}]},
            "subject": {"identifier": {"system": DONOR_ID_SYSTEM, "value": code}},
            "effectiveDateTime": f"{when}T09:00:00+09:00", **value}


def mock_bundle(donor_code: str, donation_date: date | None) -> dict:
    """모의 EMR이 돌려주는 FHIR Bundle (searchset). 기증일 기준으로 검사일을 만든다."""
    entries = []
    for (months, days), cr, unit, egfr, up in (MOCK_RESULTS.get(donor_code, []) if donation_date else []):
        tested = add_months(donation_date, months) + timedelta(days=days)
        if tested > date.today():
            continue                                         # 아직 오지 않은 날의 결과는 없다
        when = tested.isoformat()
        entries += [
            _obs(donor_code, 1, "2160-0", "Creatinine [Mass/volume] in Serum or Plasma", when,
                 {"valueQuantity": {"value": cr, "unit": unit, "system": "http://unitsofmeasure.org", "code": unit}}),
            _obs(donor_code, 2, "98979-8", "eGFR (CKD-EPI 2021)", when,
                 {"valueQuantity": {"value": egfr, "unit": "mL/min/{1.73_m2}"}}),
            _obs(donor_code, 3, "20454-5", "Protein [Presence] in Urine by Test strip", when,
                 {"valueCodeableConcept": {"text": up}}),
        ]
    return {"resourceType": "Bundle", "type": "searchset", "total": len(entries),
            "entry": [{"resource": e} for e in entries]}


# ---------- FHIR 받아 오기 ----------

def fetch_bundle(donor_code: str, donation_date: date | None = None) -> dict:
    base = (os.getenv("EMR_FHIR_BASE") or "").rstrip("/")
    if not base:
        return mock_bundle(donor_code, donation_date)
    query = urllib.parse.urlencode({"patient.identifier": f"{DONOR_ID_SYSTEM}|{donor_code}",
                                    "category": "laboratory"})
    req = urllib.request.Request(f"{base}/Observation?{query}", headers={"Accept": "application/fhir+json"})
    token = os.getenv("EMR_FHIR_TOKEN")                  # 실제 EMR: 병원이 발급한 접근 토큰
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.loads(res.read().decode("utf-8"))


# ---------- FHIR → 이음 검사 결과 (DB를 보지 않는 순수 함수) ----------

def parse_bundle(bundle: dict) -> tuple[list[dict], list[str]]:
    """
    Observation 들을 검사일별로 묶어 {tested_at, creatinine, egfr, urine_protein} 행으로 만든다.
    모르는 검사·단위·말이 안 되는 값은 버리고 이유를 problems 에 적는다.
    """
    by_day: dict[str, dict] = {}
    problems: list[str] = []
    for entry in bundle.get("entry", []):
        r = entry.get("resource", {})
        if r.get("resourceType") != "Observation" or r.get("status") not in ("final", "amended", "corrected"):
            continue
        codes = [c.get("code") for c in r.get("code", {}).get("coding", [])]
        kind = next((LOINC[c] for c in codes if c in LOINC), None)
        when = (r.get("effectiveDateTime") or "")[:10]
        if kind is None or len(when) != 10:
            continue
        row = by_day.setdefault(when, {"tested_at": date.fromisoformat(when),
                                       "creatinine": None, "egfr": None, "urine_protein": None})
        if kind == "urine_protein":
            text = (r.get("valueCodeableConcept", {}).get("text") or "").strip().lower()
            value = UP_TEXT.get(text)
            if value is None:
                problems.append(f"{when} 소변 단백 값을 알 수 없음: {text!r}")
                continue
            row["urine_protein"] = value
            continue
        q = r.get("valueQuantity", {})
        value, unit = q.get("value"), (q.get("unit") or q.get("code") or "").replace("µ", "u")
        if value is None:
            continue
        if kind == "creatinine":
            if unit in ("umol/L", "μmol/L"):
                value = round(value / 88.4, 2)            # µmol/L → mg/dL
            elif unit != "mg/dL":
                problems.append(f"{when} 크레아티닌 단위를 알 수 없음: {unit}")
                continue
            if not 0 < value < 20:
                problems.append(f"{when} 크레아티닌 값이 범위를 벗어남: {value}")
                continue
            row["creatinine"] = value
        else:
            if not 0 <= value <= 200:
                problems.append(f"{when} eGFR 값이 범위를 벗어남: {value}")
                continue
            row["egfr"] = int(round(value))
    rows = sorted(by_day.values(), key=lambda x: x["tested_at"])
    return rows, problems

"""
eCRF(전자 증례기록서)로 옮기기 쉬운 형식 만들기.

임상연구 eCRF(예: REDCap, iCReaT)는 보통 '대상자 번호 × 방문(이벤트)' 한 줄에 정해진 변수 이름과
숫자 코드로 값을 넣는다. 여기서는 REDCap 가져오기(Import) 형식을 기준으로 만든다.
  - ecrf_rows       : 입력용 표 (record_id, redcap_event_name, 변수들)
  - DICTIONARY      : 변수 정의서 (REDCap Data Dictionary 열 이름 그대로)
이름·연락처 같은 직접 식별 정보는 넣지 않고 공여자 코드(가명 번호)만 쓴다.
"""
from datetime import timedelta

import pandas as pd

EVENTS = {"기증 후 6개월": "month6_arm_1", "기증 후 1년": "year1_arm_1",
          "기증 후 2년": "year2_arm_1", "기증 후 3년": "year3_arm_1"}
SEX_CODE = {"M": 1, "F": 2}
UPROT_CODE = {"Negative": 0, "Trace": 1, "1+": 2, "2+": 3, "3+": 4}
STATUS_CODE = {"done": 1, "window": 2, "upcoming": 3, "missed": 4}
HOME_WINDOW_DAYS = 14          # 방문일 전 14일 동안의 가정혈압 평균

COLUMNS = ["record_id", "redcap_event_name",
           "birth_year", "sex", "donation_date", "pre_egfr",                       # 기본 정보 (baseline)
           "visit_due", "visit_date", "visit_status",                              # 추적 방문
           "scr", "egfr", "uprot", "home_sbp", "home_dbp", "home_n", "weight"]

# REDCap Data Dictionary 열: Variable / Field Name, Form Name, Field Type, Field Label, Choices, Field Note,
#                            Text Validation Type, Min, Max
DICTIONARY = pd.DataFrame([
    ["record_id", "baseline", "text", "공여자 번호(가명)", "", "이음 공여자 코드", "", "", ""],
    ["birth_year", "baseline", "text", "출생 연도", "", "", "integer", "1900", "2010"],
    ["sex", "baseline", "radio", "성별", "1, 남 | 2, 여", "", "", "", ""],
    ["donation_date", "baseline", "text", "기증일", "", "YYYY-MM-DD", "date_ymd", "", ""],
    ["pre_egfr", "baseline", "text", "기증 전 eGFR", "", "mL/min/1.73m²", "integer", "0", "200"],
    ["visit_due", "followup", "text", "추적검사 예정일", "", "YYYY-MM-DD", "date_ymd", "", ""],
    ["visit_date", "followup", "text", "추적검사 시행일", "", "YYYY-MM-DD, 미시행이면 빈칸", "date_ymd", "", ""],
    ["visit_status", "followup", "radio", "추적검사 상태", "1, 완료 | 2, 검사 기간 | 3, 예정 | 4, 놓침", "", "", "", ""],
    ["scr", "followup", "text", "혈청 크레아티닌", "", "mg/dL", "number", "0", "20"],
    ["egfr", "followup", "text", "eGFR", "", "mL/min/1.73m²", "integer", "0", "200"],
    ["uprot", "followup", "radio", "소변 단백(딥스틱)", "0, 음성 | 1, 미량 | 2, 1+ | 3, 2+ | 4, 3+", "", "", "", ""],
    ["home_sbp", "followup", "text", "가정 수축기 혈압 평균", "", f"시행일 전 {HOME_WINDOW_DAYS}일, mmHg", "number", "50", "260"],
    ["home_dbp", "followup", "text", "가정 이완기 혈압 평균", "", f"시행일 전 {HOME_WINDOW_DAYS}일, mmHg", "number", "30", "160"],
    ["home_n", "followup", "text", "평균에 쓴 가정혈압 횟수", "", "", "integer", "0", "", ],
    ["weight", "followup", "text", "체중", "", f"시행일 전 {HOME_WINDOW_DAYS}일 중 마지막, kg", "number", "20", "300"],
], columns=["Variable / Field Name", "Form Name", "Field Type", "Field Label",
            "Choices, Calculations, OR Slider Labels", "Field Note",
            "Text Validation Type OR Show Slider Number", "Text Validation Min", "Text Validation Max"])


def ecrf_rows(donors: pd.DataFrame, visits: pd.DataFrame, labs: pd.DataFrame, home: pd.DataFrame) -> pd.DataFrame:
    """
    donors : donor_code, birth_year, sex, donation_date, predonation_egfr
    visits : donor_code, visit_name, due_date, completed_date, status(영문)
    labs   : donor_code, tested_at, creatinine, egfr, urine_protein, visit_name
    home   : donor_code, measured_at, systolic_bp, diastolic_bp, weight_kg
    """
    rows = []
    for d in donors.itertuples():
        rows.append({"record_id": d.donor_code, "redcap_event_name": "baseline_arm_1",
                     "birth_year": d.birth_year, "sex": SEX_CODE.get(d.sex),
                     "donation_date": d.donation_date, "pre_egfr": d.predonation_egfr})

    for v in visits.itertuples():
        if v.visit_name not in EVENTS:
            continue
        row = {"record_id": v.donor_code, "redcap_event_name": EVENTS[v.visit_name],
               "visit_due": v.due_date, "visit_status": STATUS_CODE.get(v.status)}
        done = v.completed_date if pd.notna(v.completed_date) else None
        if done is not None:
            row["visit_date"] = done
            lab = labs[(labs["donor_code"] == v.donor_code) & (labs["visit_name"] == v.visit_name)]
            if not lab.empty:
                last = lab.iloc[-1]
                row["scr"] = last["creatinine"]
                row["egfr"] = last["egfr"]
                row["uprot"] = UPROT_CODE.get(last["urine_protein"])
            h = home[(home["donor_code"] == v.donor_code)
                     & (home["measured_at"] <= done)
                     & (home["measured_at"] > done - timedelta(days=HOME_WINDOW_DAYS))]
            bp = h.dropna(subset=["systolic_bp", "diastolic_bp"])
            row["home_n"] = len(bp)
            if len(bp):
                row["home_sbp"] = round(bp["systolic_bp"].mean(), 1)
                row["home_dbp"] = round(bp["diastolic_bp"].mean(), 1)
            w = h.dropna(subset=["weight_kg"])
            if len(w):
                row["weight"] = w.iloc[-1]["weight_kg"]
        rows.append(row)

    out = pd.DataFrame(rows, columns=COLUMNS)
    order = {"baseline_arm_1": 0, **{e: i + 1 for i, e in enumerate(EVENTS.values())}}
    out["_o"] = out["redcap_event_name"].map(order)
    out = out.sort_values(["record_id", "_o"]).drop(columns="_o").reset_index(drop=True)
    # 날짜는 YYYY-MM-DD 글자로, 숫자 코드는 빈칸을 허용하는 정수로
    for col in ["donation_date", "visit_due", "visit_date"]:
        out[col] = out[col].map(lambda x: x.isoformat() if pd.notna(x) and hasattr(x, "isoformat") else "")
    for col in ["birth_year", "sex", "pre_egfr", "visit_status", "egfr", "uprot", "home_n"]:
        out[col] = pd.to_numeric(out[col]).astype("Int64")
    return out

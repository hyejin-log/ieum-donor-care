import pandas as pd
import requests
import streamlit as st

API = "http://127.0.0.1:8000"   # FastAPI 서버 주소

st.title("신장 공여자 건강 기록")
donor_code = st.text_input("공여자 코드", "D-001")

# ---------- 측정값 입력 ----------
st.subheader("측정값 입력")
with st.form("measurement_form"):
    measured_at = st.date_input("측정 날짜")
    c1, c2 = st.columns(2)
    sbp = c1.number_input("수축기 혈압 (mmHg)", 50, 260, 120)
    dbp = c2.number_input("이완기 혈압 (mmHg)", 30, 160, 80)
    weight = c1.number_input("체중 (kg)", 20.0, 300.0, 60.0, step=0.1)
    creatinine = c2.number_input("크레아티닌 (mg/dL)", 0.1, 20.0, 0.9, step=0.01)
    egfr = c1.number_input("eGFR", 0, 200, 90)
    urine_protein = c2.selectbox("소변 단백", ["Negative", "Trace", "1+", "2+", "3+"])
    submitted = st.form_submit_button("저장")

if submitted:
    res = requests.post(f"{API}/measurements", json={
        "donor_code": donor_code,
        "measured_at": measured_at.isoformat(),
        "systolic_bp": sbp,
        "diastolic_bp": dbp,
        "weight_kg": weight,
        "creatinine": creatinine,
        "egfr": egfr,
        "urine_protein": urine_protein,
    })
    if res.ok:
        st.success("저장되었습니다.")
    else:
        st.error(f"저장 실패: {res.text}")

# ---------- 기록 추이 ----------
st.subheader("기록 추이")
res = requests.get(f"{API}/donors/{donor_code}/measurements")
if res.ok and res.json():
    df = pd.DataFrame(res.json())
    df["measured_at"] = pd.to_datetime(df["measured_at"])
    df = df.set_index("measured_at")
    st.write("혈압")
    st.line_chart(df[["systolic_bp", "diastolic_bp"]])
    st.write("eGFR")
    st.line_chart(df[["egfr"]])
    st.dataframe(df)
else:
    st.info("아직 기록이 없습니다.")
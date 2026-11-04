import io
import json
import os
from datetime import date, timedelta
from typing import Literal

import pandas as pd
import agent
import risk
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

load_dotenv()                                  # .env 파일 읽기

app = FastAPI(title="이음 - Donor Care API")

# 예정일 앞뒤 60일 안에 받으면 기한 내 검사로 본다 (OPTN 공여자 추적 기준)
GRACE_DAYS = 60
# 다음 검사 알림을 보내기 시작하는 시점 (예정일 30일 전)
REMIND_DAYS = 30
# 자가 기록률을 계산하는 기간 (최근 12주)
RECENT_WEEKS = 12

# ---------- 경고 규칙의 기준값 ----------
# 데모용 기준값이다. 실제 운영 전에는 이식센터 의료진의 검토와 승인이 필요하다.
BP_HOME_SYS, BP_HOME_DIA = 135, 85        # 가정혈압 고혈압 기준 (최근 2회 평균)
BP_CRISIS_SYS, BP_CRISIS_DIA = 180, 110   # 한 번이라도 이 이상이면 즉시 확인
EGFR_LOW = 45                             # eGFR 45 미만 (CKD 3b 단계 이하)
EGFR_DROP_PERCENT = 20                    # 직전 검사보다 20% 이상 감소
PROTEIN_WARN = {"1+"}                     # 소변 단백 1+ 는 주의
PROTEIN_URGENT = {"2+", "3+"}             # 2+ 이상은 즉시 확인
WEIGHT_GAIN_PERCENT = 5                   # 최근 12주 사이 체중 5% 이상 증가


def get_conn():
    """데이터베이스에 접속한다. 결과는 {칸 이름: 값} 형태로 받는다."""
    return psycopg.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        row_factory=dict_row,
    )


def get_donor(conn, donor_code: str) -> dict:
    """공여자 코드로 공여자를 찾는다. 없으면 404."""
    donor = conn.execute(
        "SELECT donor_id, donor_code, donation_date, predonation_egfr FROM donors WHERE donor_code = %s",
        (donor_code,),
    ).fetchone()
    if donor is None:
        raise HTTPException(status_code=404, detail="공여자 코드를 찾을 수 없습니다")
    return donor


def visit_status(due: date, completed: date | None, today: date) -> str:
    """추적검사 상태: done(완료) / missed(놓침) / window(검사 기간) / upcoming(예정)"""
    if completed:
        return "done"
    if today > due + timedelta(days=GRACE_DAYS):
        return "missed"
    if today >= due - timedelta(days=GRACE_DAYS):
        return "window"
    return "upcoming"


def week_start(d: date) -> date:
    """그 주의 월요일"""
    return d - timedelta(days=d.weekday())


def load_visits(conn, donor_id: int, today: date) -> list[dict]:
    visits = conn.execute(
        "SELECT followup_id, visit_name, due_date, completed_date FROM followups "
        "WHERE donor_id = %s ORDER BY due_date",
        (donor_id,),
    ).fetchall()
    for v in visits:
        v["status"] = visit_status(v["due_date"], v["completed_date"], today)
    return visits


# ---------- 경고 규칙 ----------

def donor_alerts(home: list[dict], labs: list[dict]) -> list[dict]:
    """
    자가 기록과 검사 결과로 경고 목록을 계산한다. DB를 보지 않는 순수 함수.
    home: 최근 12주 자가 기록 (측정일순), labs: 전체 검사 결과 (검사일순)
    level: urgent(즉시 확인) / warn(주의) / info(참고)
    """
    alerts = []

    # 혈압: 매우 높은 값이 한 번이라도 있으면 urgent
    for r in home:
        sys_, dia = r["systolic_bp"], r["diastolic_bp"]
        if (sys_ or 0) >= BP_CRISIS_SYS or (dia or 0) >= BP_CRISIS_DIA:
            alerts.append({"id": f"bp-crisis-{r['measured_at']}", "level": "urgent", "kind": "bp",
                           "title": "혈압이 매우 높게 측정됨",
                           "detail": f"{r['measured_at']} · {sys_}/{dia} mmHg"})

    # 혈압: 최근 2회 평균이 가정혈압 기준 이상이면 warn
    bp = [r for r in home if r["systolic_bp"] is not None and r["diastolic_bp"] is not None][-2:]
    if len(bp) == 2:
        avg_sys = sum(r["systolic_bp"] for r in bp) / 2
        avg_dia = sum(r["diastolic_bp"] for r in bp) / 2
        if avg_sys >= BP_HOME_SYS or avg_dia >= BP_HOME_DIA:
            alerts.append({"id": f"bp-high-{bp[-1]['measured_at']}", "level": "warn", "kind": "bp",
                           "title": "최근 2회 혈압 평균이 높음",
                           "detail": f"평균 {avg_sys:.0f}/{avg_dia:.0f} mmHg (기준 {BP_HOME_SYS}/{BP_HOME_DIA})"})

    # 체중: 기간 첫 기록 대비 마지막 기록이 5% 이상 증가하면 info
    w = [r for r in home if r["weight_kg"] is not None]
    if len(w) >= 2 and float(w[0]["weight_kg"]) > 0:
        gain = (float(w[-1]["weight_kg"]) - float(w[0]["weight_kg"])) / float(w[0]["weight_kg"]) * 100
        if gain >= WEIGHT_GAIN_PERCENT:
            alerts.append({"id": f"weight-{w[-1]['measured_at']}", "level": "info", "kind": "weight",
                           "title": "체중이 늘고 있음",
                           "detail": f"{w[0]['weight_kg']} → {w[-1]['weight_kg']} kg (+{gain:.1f}%)"})

    # 검사 결과: 가장 최근 결과만 판단 (예전 결과는 이미 확인된 것으로 본다)
    egfr_labs = [l for l in labs if l["egfr"] is not None]
    if egfr_labs:
        last = egfr_labs[-1]
        if last["egfr"] < EGFR_LOW:
            alerts.append({"id": f"egfr-low-{last['tested_at']}", "level": "urgent", "kind": "lab",
                           "title": f"eGFR {EGFR_LOW} 미만",
                           "detail": f"{last['tested_at']} · eGFR {last['egfr']}"})
        if len(egfr_labs) >= 2:
            prev = egfr_labs[-2]
            drop = (prev["egfr"] - last["egfr"]) / prev["egfr"] * 100
            if drop >= EGFR_DROP_PERCENT:
                alerts.append({"id": f"egfr-drop-{last['tested_at']}", "level": "warn", "kind": "lab",
                               "title": "eGFR이 직전 검사보다 크게 감소",
                               "detail": f"{prev['egfr']} → {last['egfr']} (-{drop:.0f}%)"})
    if labs and labs[-1]["urine_protein"] in PROTEIN_WARN | PROTEIN_URGENT:
        last = labs[-1]
        urgent = last["urine_protein"] in PROTEIN_URGENT
        alerts.append({"id": f"protein-{last['tested_at']}", "level": "urgent" if urgent else "warn",
                       "kind": "lab", "title": "소변 단백 양성",
                       "detail": f"{last['tested_at']} · {last['urine_protein']}"})

    order = {"urgent": 0, "warn": 1, "info": 2}
    return sorted(alerts, key=lambda a: order[a["level"]])


def load_alerts(conn, donor_id: int, today: date) -> list[dict]:
    """경고 계산에 필요한 기록을 DB에서 읽어 donor_alerts에 넘긴다."""
    since = week_start(today) - timedelta(weeks=RECENT_WEEKS - 1)
    home = conn.execute(
        "SELECT measured_at, systolic_bp, diastolic_bp, weight_kg FROM home_measurements "
        "WHERE donor_id = %s AND measured_at >= %s ORDER BY measured_at",
        (donor_id, since),
    ).fetchall()
    labs = conn.execute(
        "SELECT tested_at, egfr, urine_protein FROM lab_results WHERE donor_id = %s ORDER BY tested_at",
        (donor_id,),
    ).fetchall()
    return donor_alerts(home, labs)


# ---------- 화면 ----------

@app.get("/")
def donor_page():
    """공여자용 화면"""
    return FileResponse("static/donor.html")


@app.get("/staff")
def staff_page():
    """의료진용 화면: 공여자 목록과 검사 결과 입력"""
    return FileResponse("static/staff.html")


@app.get("/dashboard")
def dashboard_page():
    """의료진용 공여자 상세 화면"""
    return FileResponse("static/dashboard.html")


def _key_check(key: str | None) -> dict | None:
    """키 값 대신 모양만 확인한다: Groq 키는 gsk_ 로 시작한다."""
    if not key:
        return None
    k = key.strip().strip('"\'').strip()
    return {"length": len(k), "starts_with_gsk_": k.startswith("gsk_"),
            "has_space_or_quote": any(c in k for c in " '\"<>")}


@app.get("/health")
def health():
    """서버가 살아 있는지 확인"""
    return {"status": "ok", "llm_provider": agent.LLM_PROVIDER,
            "llm_model": agent.GROQ_MODEL if agent.LLM_PROVIDER == "groq" else agent.OLLAMA_MODEL,
            "groq_key_set": bool(os.getenv("GROQ_API_KEY")),          # 키 값은 보여주지 않는다
            "groq_key_check": _key_check(os.getenv("GROQ_API_KEY")),
            "llm_last_error": agent.LAST_LLM_ERROR}


@app.get("/health/groq-models")
def groq_model_list():
    """이 키로 쓸 수 있는 Groq 모델 목록 (모델 이름만)"""
    try:
        return {"current": agent.GROQ_MODEL, "available": agent.groq_models()}
    except Exception as e:
        return {"current": agent.GROQ_MODEL, "error": type(e).__name__}


# ---------- 공여자·추적검사 ----------

@app.get("/donors")
def list_donors():
    """공여자 목록"""
    with get_conn() as conn:
        return conn.execute(
            "SELECT donor_id, donor_code, birth_year, sex, donation_date, predonation_egfr "
            "FROM donors ORDER BY donor_code"
        ).fetchall()


@app.get("/followups/overdue")
def list_overdue():
    """예정일 + 60일이 지났는데 받지 않은 추적검사 (의료진용)"""
    with get_conn() as conn:
        return conn.execute(
            "SELECT d.donor_code, f.visit_name, f.due_date "
            "FROM followups f JOIN donors d ON d.donor_id = f.donor_id "
            "WHERE f.due_date + %s < CURRENT_DATE AND f.completed_date IS NULL "
            "ORDER BY f.due_date",
            (GRACE_DAYS,),
        ).fetchall()


@app.get("/donors/{donor_code}/summary")
def donor_summary(donor_code: str):
    """공여자 요약: 기증 후 일수, 추적검사 여정, 최근 검사 결과와 기준값 대비 eGFR"""
    today = date.today()
    with get_conn() as conn:
        donor = get_donor(conn, donor_code)
        visits = load_visits(conn, donor["donor_id"], today)
        latest = conn.execute(
            "SELECT tested_at, egfr FROM lab_results "
            "WHERE donor_id = %s AND egfr IS NOT NULL ORDER BY tested_at DESC LIMIT 1",
            (donor["donor_id"],),
        ).fetchone()

    percent = None
    if latest and donor["predonation_egfr"]:
        percent = round(latest["egfr"] / donor["predonation_egfr"] * 100)

    return {
        "donor_code": donor["donor_code"],
        "donation_date": donor["donation_date"],
        "days_since_donation": (today - donor["donation_date"]).days,
        "predonation_egfr": donor["predonation_egfr"],
        "latest_egfr": latest["egfr"] if latest else None,
        "latest_egfr_date": latest["tested_at"] if latest else None,
        "egfr_percent_of_predonation": percent,
        "visits": visits,
    }


# ---------- 공여자 자가 기록: 혈압·체중 ----------

class HomeIn(BaseModel):
    """공여자가 집에서 잰 값. 말이 안 되는 값은 저장 전에 거절한다."""
    donor_code: str
    measured_at: date
    systolic_bp: int | None = Field(None, ge=50, le=260)
    diastolic_bp: int | None = Field(None, ge=30, le=160)
    weight_kg: float | None = Field(None, gt=20, lt=300)

    @field_validator("measured_at")
    @classmethod
    def not_in_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("측정 날짜는 오늘 이후일 수 없습니다")
        return v


@app.post("/home-measurements")
def create_home(m: HomeIn):
    """자가 기록 저장. 같은 날짜 기록이 있으면 새 값으로 덮어쓴다."""
    with get_conn() as conn:
        donor = get_donor(conn, m.donor_code)
        existing = conn.execute(
            "SELECT 1 FROM home_measurements WHERE donor_id = %s AND measured_at = %s",
            (donor["donor_id"], m.measured_at),
        ).fetchone()
        row = conn.execute(
            "INSERT INTO home_measurements (donor_id, measured_at, systolic_bp, diastolic_bp, weight_kg) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (donor_id, measured_at) DO UPDATE SET "
            "systolic_bp = EXCLUDED.systolic_bp, diastolic_bp = EXCLUDED.diastolic_bp, "
            "weight_kg = EXCLUDED.weight_kg "
            "RETURNING home_id",
            (donor["donor_id"], m.measured_at, m.systolic_bp, m.diastolic_bp, m.weight_kg),
        ).fetchone()
    return {"home_id": row["home_id"], "updated": existing is not None}


@app.get("/donors/{donor_code}/home")
def list_home(donor_code: str):
    """공여자 자가 기록 (날짜순)"""
    with get_conn() as conn:
        donor = get_donor(conn, donor_code)
        return conn.execute(
            "SELECT measured_at, systolic_bp, diastolic_bp, weight_kg FROM home_measurements "
            "WHERE donor_id = %s ORDER BY measured_at",
            (donor["donor_id"],),
        ).fetchall()


# ---------- 병원 검사 결과: 연구 담당자 입력 ----------

class LabIn(BaseModel):
    """병원 검사 결과지의 값"""
    donor_code: str
    tested_at: date
    creatinine: float | None = Field(None, gt=0, lt=20)
    egfr: int | None = Field(None, ge=0, le=200)
    urine_protein: Literal["Negative", "Trace", "1+", "2+", "3+"] | None = None
    entered_by: str = Field(min_length=1, max_length=40)

    @field_validator("tested_at")
    @classmethod
    def not_in_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("검사 날짜는 오늘 이후일 수 없습니다")
        return v


@app.post("/labs")
def create_lab(lab: LabIn):
    """
    검사 결과 저장. 같은 날짜 결과가 있으면 덮어쓴다.
    아직 완료되지 않은 추적검사 중 검사 기간이 시작된 가장 이른 것을 이 결과로 '완료' 처리한다.
    """
    with get_conn() as conn:
        donor = get_donor(conn, lab.donor_code)

        visit = conn.execute(
            "SELECT followup_id, visit_name FROM followups "
            "WHERE donor_id = %s AND completed_date IS NULL AND due_date - %s <= %s "
            "ORDER BY due_date LIMIT 1",
            (donor["donor_id"], GRACE_DAYS, lab.tested_at),
        ).fetchone()
        followup_id = visit["followup_id"] if visit else None

        row = conn.execute(
            "INSERT INTO lab_results "
            "(donor_id, tested_at, creatinine, egfr, urine_protein, followup_id, entered_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (donor_id, tested_at) DO UPDATE SET "
            "creatinine = EXCLUDED.creatinine, egfr = EXCLUDED.egfr, "
            "urine_protein = EXCLUDED.urine_protein, entered_by = EXCLUDED.entered_by, "
            "followup_id = COALESCE(lab_results.followup_id, EXCLUDED.followup_id) "
            "RETURNING lab_id, followup_id",
            (donor["donor_id"], lab.tested_at, lab.creatinine, lab.egfr,
             lab.urine_protein, followup_id, lab.entered_by),
        ).fetchone()

        completed = None
        if visit and row["followup_id"] == followup_id:
            conn.execute(
                "UPDATE followups SET completed_date = %s WHERE followup_id = %s",
                (lab.tested_at, followup_id),
            )
            completed = visit["visit_name"]

    return {"lab_id": row["lab_id"], "completed_visit": completed}


@app.get("/donors/{donor_code}/labs")
def list_labs(donor_code: str):
    """공여자 검사 결과 (날짜순)"""
    with get_conn() as conn:
        donor = get_donor(conn, donor_code)
        return conn.execute(
            "SELECT l.tested_at, l.creatinine, l.egfr, l.urine_protein, l.entered_by, f.visit_name "
            "FROM lab_results l LEFT JOIN followups f ON f.followup_id = l.followup_id "
            "WHERE l.donor_id = %s ORDER BY l.tested_at",
            (donor["donor_id"],),
        ).fetchall()


@app.get("/donors/{donor_code}/alerts")
def list_alerts(donor_code: str):
    """의료진용 경고 목록 (혈압 · 체중 · 검사 결과 규칙)"""
    with get_conn() as conn:
        donor = get_donor(conn, donor_code)
        return load_alerts(conn, donor["donor_id"], date.today())


# ---------- 공여자 알림 ----------

@app.get("/donors/{donor_code}/notifications")
def donor_notifications(donor_code: str):
    """
    공여자에게 보여줄 알림을 지금 상태로 계산한다.
    id는 같은 내용이면 항상 같아서, 화면에서 '이미 본 알림'을 구분할 수 있다.
    """
    today = date.today()
    this_week = week_start(today)
    with get_conn() as conn:
        donor = get_donor(conn, donor_code)
        visits = load_visits(conn, donor["donor_id"], today)
        last_home = conn.execute(
            "SELECT max(measured_at) AS d FROM home_measurements WHERE donor_id = %s",
            (donor["donor_id"],),
        ).fetchone()["d"]
        bp_alerts = [a for a in load_alerts(conn, donor["donor_id"], today) if a["kind"] == "bp"]
        recent_labs = conn.execute(
            "SELECT lab_id, tested_at FROM lab_results "
            "WHERE donor_id = %s AND created_at >= now() - interval '14 days' "
            "ORDER BY created_at DESC LIMIT 1",
            (donor["donor_id"],),
        ).fetchall()

    items = []
    for v in visits:
        if v["status"] == "missed":
            items.append({"id": f"missed-{v['followup_id']}", "level": "urgent",
                          "title": f"{v['visit_name']} 검사를 아직 받지 않았어요",
                          "body": "이식센터에 연락해 검사 일정을 잡아 주세요."})
        elif v["status"] == "window":
            items.append({"id": f"window-{v['followup_id']}", "level": "warn",
                          "title": f"{v['visit_name']} 검사 기간이에요",
                          "body": f"{(v['due_date'] + timedelta(days=GRACE_DAYS)).isoformat()}까지 검사를 받아 주세요."})
        elif v["status"] == "upcoming" and (v["due_date"] - today).days <= REMIND_DAYS + GRACE_DAYS:
            items.append({"id": f"soon-{v['followup_id']}", "level": "info",
                          "title": f"{v['visit_name']} 검사가 다가와요",
                          "body": f"예정일 {v['due_date'].isoformat()} · 미리 예약해 두세요."})

    if last_home is None or last_home < this_week:
        weeks = None if last_home is None else (this_week - week_start(last_home)).days // 7
        items.append({"id": f"weekly-{this_week.isoformat()}", "level": "info",
                      "title": "이번 주 혈압·체중을 기록해 주세요",
                      "body": "아직 기록이 없어요." if weeks is None else f"마지막 기록은 {weeks}주 전이에요."})

    # 혈압 경고만 공여자에게도 보여준다. 검사 결과 해석은 의료진이 전달한다.
    for a in bp_alerts:
        if a["level"] == "urgent":
            items.insert(0, {"id": a["id"], "level": "urgent", "title": "혈압이 매우 높게 측정되었어요",
                             "body": "잠시 쉬고 다시 재 보세요. 계속 높거나 두통·가슴 통증이 있으면 바로 병원에 연락해 주세요."})
        else:
            items.append({"id": a["id"], "level": "warn", "title": "최근 혈압이 높게 측정되고 있어요",
                          "body": "같은 시간에 다시 재 보고, 계속 높으면 이식센터에 알려 주세요."})

    for lab in recent_labs:
        items.append({"id": f"lab-{lab['lab_id']}", "level": "ok",
                      "title": "새 검사 결과가 등록됐어요",
                      "body": f"{lab['tested_at'].isoformat()} 검사 결과를 확인해 보세요."})
    return items


# ---------- 안내 Agent (LLM) ----------

class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1000)


class ChatIn(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=20)   # 최근 대화만 보낸다


def chat_tools(donor_code: str) -> dict:
    """Agent가 쓸 조회 함수. 공여자 코드를 여기서 고정하므로 다른 공여자의 기록은 볼 수 없다."""
    return {
        "get_donation_summary": lambda: donor_summary(donor_code),
        "get_home_records": lambda: list_home(donor_code)[-12:],
        "get_lab_results": lambda: list_labs(donor_code),
        "get_notifications": lambda: donor_notifications(donor_code),
    }


@app.post("/donors/{donor_code}/chat")
def donor_chat(donor_code: str, chat: ChatIn):
    """공여자 질문에 안내 Agent가 답한다. 도구는 이 공여자의 기록만 조회하도록 고정한다."""
    with get_conn() as conn:
        get_donor(conn, donor_code)                     # 없는 공여자면 404
    if chat.messages[-1].role != "user":
        raise HTTPException(status_code=422, detail="마지막 메시지는 질문이어야 합니다")

    impl = chat_tools(donor_code)
    result = agent.run_agent([m.model_dump() for m in chat.messages], impl)
    result["disclaimer"] = agent.DISCLAIMER
    return result


@app.post("/donors/{donor_code}/chat/stream")
def donor_chat_stream(donor_code: str, chat: ChatIn):
    """donor_chat과 같지만, 답을 만들어지는 대로 한 줄씩(NDJSON) 보낸다."""
    with get_conn() as conn:
        get_donor(conn, donor_code)
    if chat.messages[-1].role != "user":
        raise HTTPException(status_code=422, detail="마지막 메시지는 질문이어야 합니다")
    impl = chat_tools(donor_code)
    messages = [m.model_dump() for m in chat.messages]

    def lines():
        for event in agent.run_agent_stream(messages, impl):
            yield json.dumps(event, ensure_ascii=False, default=str) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson")


@app.post("/donors/{donor_code}/chat/warmup")
def agent_warmup(donor_code: str):
    """대화 창을 열 때 호출: 로컬 모델에 이 공여자의 자료를 미리 읽혀 둔다."""
    with get_conn() as conn:
        get_donor(conn, donor_code)
    return {"ready": agent.warm_up(chat_tools(donor_code))}


# ---------- 의료진: 전체 공여자 현황 ----------

@app.get("/staff/overview")
def staff_overview():
    """공여자별 상태 요약. 검사를 놓쳤거나 기록이 끊긴 공여자가 위로 온다."""
    today = date.today()
    this_week = week_start(today)
    rows = []
    with get_conn() as conn:
        weekly = weekly_records(conn, today)                 # 공여자 × 최근 12주 기록 여부 (pandas)
        donors = conn.execute(
            "SELECT donor_id, donor_code, birth_year, donation_date, predonation_egfr FROM donors ORDER BY donor_code"
        ).fetchall()
        for d in donors:
            visits = load_visits(conn, d["donor_id"], today)
            last_home = conn.execute(
                "SELECT max(measured_at) AS d FROM home_measurements WHERE donor_id = %s",
                (d["donor_id"],),
            ).fetchone()["d"]
            latest = conn.execute(
                "SELECT tested_at, egfr FROM lab_results WHERE donor_id = %s AND egfr IS NOT NULL "
                "ORDER BY tested_at DESC LIMIT 1",
                (d["donor_id"],),
            ).fetchone()

            missed = [v for v in visits if v["status"] == "missed"]
            window = [v for v in visits if v["status"] == "window"]
            nxt = next((v for v in visits if v["status"] in ("window", "upcoming")), None)
            weeks_since_home = None if last_home is None else (this_week - week_start(last_home)).days // 7
            alerts = load_alerts(conn, d["donor_id"], today)
            status = "missed" if missed else "window" if window else "ok"
            percent = None
            if latest and d["predonation_egfr"]:
                percent = round(latest["egfr"] / d["predonation_egfr"] * 100)

            rows.append({
                "donor_code": d["donor_code"],
                "donation_date": d["donation_date"],
                "days_since_donation": (today - d["donation_date"]).days,
                "status": status,
                "missed_visits": [v["visit_name"] for v in missed],
                "next_visit": None if nxt is None else {
                    "visit_name": nxt["visit_name"], "due_date": nxt["due_date"],
                    "d_day": (nxt["due_date"] - today).days, "status": nxt["status"],
                },
                "latest_egfr": latest["egfr"] if latest else None,
                "latest_egfr_date": latest["tested_at"] if latest else None,
                "egfr_percent_of_predonation": percent,
                "last_home_date": last_home,
                "weeks_since_home": weeks_since_home,
                "weekly_records": [bool(x) for x in weekly.loc[d["donor_code"]]],
                "record_rate_percent": round(weekly.loc[d["donor_code"]].mean() * 100),
                "alerts": alerts,
                # ML: 다음 추적검사를 놓칠 위험 (합성 데이터로 학습한 시연용 모델)
                "dropout_risk": risk.predict(risk.donor_features(
                    visits, float(weekly.loc[d["donor_code"]].mean()), weeks_since_home,
                    d["donation_date"], d["birth_year"], any(a["kind"] == "bp" for a in alerts), today)),
            })

    order = {"missed": 0, "window": 1, "ok": 2}
    urgent = lambda r: sum(a["level"] == "urgent" for a in r["alerts"])
    warn = lambda r: sum(a["level"] == "warn" for a in r["alerts"])
    rows.sort(key=lambda r: (order[r["status"]], -urgent(r), -warn(r), -(r["weeks_since_home"] or 0)))
    return rows


# ---------- 연구 데이터 내보내기 (pandas) ----------

STATUS_KO = {"done": "완료", "missed": "놓침", "window": "검사 기간", "upcoming": "예정"}


def query_df(conn, sql: str, columns: list[str], params: tuple = ()) -> pd.DataFrame:
    """SQL 결과를 DataFrame으로. 결과가 없어도 열 이름은 유지한다."""
    return pd.DataFrame(conn.execute(sql, params).fetchall(), columns=columns)


def weekly_records(conn, today: date, weeks: int = RECENT_WEEKS) -> pd.DataFrame:
    """
    공여자 × 최근 N주 표를 만든다. 그 주(월~일)에 자가 기록이 한 번이라도 있으면 True.
    행: 공여자 코드, 열: 각 주의 월요일 (오래된 주 → 이번 주)
    """
    first_week = week_start(today) - timedelta(weeks=weeks - 1)
    week_index = pd.date_range(first_week, periods=weeks, freq="7D")       # 월요일 N개
    codes = query_df(conn, "SELECT donor_code FROM donors ORDER BY donor_code", ["donor_code"])["donor_code"]
    home = query_df(conn,
        "SELECT d.donor_code, h.measured_at FROM home_measurements h "
        "JOIN donors d ON d.donor_id = h.donor_id WHERE h.measured_at >= %s",
        ["donor_code", "measured_at"], (first_week,))

    if home.empty:
        return pd.DataFrame(False, index=codes, columns=week_index)

    # 측정일 → 그 주의 월요일 ("W-SUN" = 일요일에 끝나는 주, start_time = 그 주 월요일)
    home["week"] = pd.to_datetime(home["measured_at"]).dt.to_period("W-SUN").dt.start_time
    counts = pd.crosstab(home["donor_code"], home["week"])                  # 공여자 × 주 기록 건수
    return counts.reindex(index=codes, columns=week_index, fill_value=0) > 0


def build_research_tables(conn, today: date) -> dict[str, pd.DataFrame]:
    """공여자 요약 · 검사 결과 · 자가 기록 · 주간 기록 · 추적검사 표 5개를 만든다."""
    donors = query_df(conn,
        "SELECT donor_id, donor_code, birth_year, sex, donation_date, predonation_egfr "
        "FROM donors ORDER BY donor_code",
        ["donor_id", "donor_code", "birth_year", "sex", "donation_date", "predonation_egfr"])
    labs = query_df(conn,
        "SELECT d.donor_code, l.tested_at, l.creatinine, l.egfr, l.urine_protein, f.visit_name, l.entered_by "
        "FROM lab_results l JOIN donors d ON d.donor_id = l.donor_id "
        "LEFT JOIN followups f ON f.followup_id = l.followup_id "
        "ORDER BY d.donor_code, l.tested_at",
        ["donor_code", "tested_at", "creatinine", "egfr", "urine_protein", "visit_name", "entered_by"])
    home = query_df(conn,
        "SELECT d.donor_code, h.measured_at, h.systolic_bp, h.diastolic_bp, h.weight_kg "
        "FROM home_measurements h JOIN donors d ON d.donor_id = h.donor_id "
        "ORDER BY d.donor_code, h.measured_at",
        ["donor_code", "measured_at", "systolic_bp", "diastolic_bp", "weight_kg"])
    visits = query_df(conn,
        "SELECT d.donor_code, f.visit_name, f.due_date, f.completed_date "
        "FROM followups f JOIN donors d ON d.donor_id = f.donor_id "
        "ORDER BY d.donor_code, f.due_date",
        ["donor_code", "visit_name", "due_date", "completed_date"])

    # 자료형 정리: DB의 NUMERIC은 Decimal로 오므로 숫자(float)로 바꾼다
    labs["creatinine"] = pd.to_numeric(labs["creatinine"])
    home["weight_kg"] = pd.to_numeric(home["weight_kg"])

    # 추적검사 상태: 화면과 같은 규칙(visit_status)을 한 행씩 적용
    visits["status"] = [visit_status(due, done if pd.notna(done) else None, today)
                        for due, done in zip(visits["due_date"], visits["completed_date"])]
    visit_counts = (visits.groupby("donor_code")["status"]
                    .value_counts().unstack(fill_value=0)
                    .reindex(columns=["done", "missed"], fill_value=0)
                    .rename(columns={"done": "completed_visits", "missed": "missed_visits"}))

    # 최근 eGFR: 검사일순으로 정렬된 표에서 공여자별 마지막 값
    latest = (labs.dropna(subset=["egfr"]).groupby("donor_code")
              .agg(latest_egfr=("egfr", "last"), latest_egfr_date=("tested_at", "last")))

    # 최근 12주 기록률: weekly_records 표에서 공여자별로 True(기록한 주)의 개수
    weekly = weekly_records(conn, today)
    first_week = weekly.columns[0].date()
    recent = home[home["measured_at"] >= first_week]
    adherence = pd.DataFrame({
        "weeks_recorded": weekly.sum(axis=1),
        "mean_systolic_bp": recent.groupby("donor_code")["systolic_bp"].mean(),
    }).rename_axis("donor_code").reset_index()

    # 경고: 공여자마다 경고 제목을 한 칸에 이어 적는다
    donors["alerts"] = [", ".join(a["title"] for a in load_alerts(conn, int(i), today))
                        for i in donors["donor_id"]]

    summary = (donors.drop(columns="donor_id")
               .merge(latest, on="donor_code", how="left")
               .merge(adherence, on="donor_code", how="left")
               .merge(visit_counts, on="donor_code", how="left"))
    summary["days_since_donation"] = [(today - d).days for d in summary["donation_date"]]
    summary["egfr_percent"] = (summary["latest_egfr"] / summary["predonation_egfr"] * 100).round()
    summary["weeks_recorded"] = summary["weeks_recorded"].fillna(0).astype(int)
    summary["record_rate_percent"] = (summary["weeks_recorded"] / RECENT_WEEKS * 100).round()
    summary["mean_systolic_bp"] = summary["mean_systolic_bp"].round(1)
    for col in ["completed_visits", "missed_visits"]:
        summary[col] = summary[col].fillna(0).astype(int)

    summary = summary[["donor_code", "birth_year", "sex", "donation_date", "days_since_donation",
                       "predonation_egfr", "latest_egfr", "latest_egfr_date", "egfr_percent",
                       "weeks_recorded", "record_rate_percent", "mean_systolic_bp",
                       "completed_visits", "missed_visits", "alerts"]]

    return {
        "공여자 요약": summary.rename(columns={
            "donor_code": "공여자", "birth_year": "출생연도", "sex": "성별", "donation_date": "기증일",
            "days_since_donation": "기증 후 일수", "predonation_egfr": "기증 전 eGFR",
            "latest_egfr": "최근 eGFR", "latest_egfr_date": "최근 eGFR 검사일", "egfr_percent": "기증 전 대비 %",
            "weeks_recorded": f"최근 {RECENT_WEEKS}주 중 기록한 주", "record_rate_percent": "기록률 %",
            "mean_systolic_bp": "평균 수축기 혈압", "completed_visits": "완료 추적검사",
            "missed_visits": "놓친 추적검사", "alerts": "경고"}),
        "검사 결과": labs.rename(columns={
            "donor_code": "공여자", "tested_at": "검사일", "creatinine": "크레아티닌", "egfr": "eGFR",
            "urine_protein": "소변 단백", "visit_name": "추적검사", "entered_by": "입력자"}),
        "자가 기록": home.rename(columns={
            "donor_code": "공여자", "measured_at": "측정일", "systolic_bp": "수축기",
            "diastolic_bp": "이완기", "weight_kg": "체중"}),
        "주간 기록": weekly.rename(columns=lambda w: f"{w:%m.%d}주")
                         .replace({True: "O", False: ""})
                         .rename_axis("공여자").reset_index(),
        "추적검사": visits.assign(status=visits["status"].map(STATUS_KO)).rename(columns={
            "donor_code": "공여자", "visit_name": "추적검사", "due_date": "예정일",
            "completed_date": "완료일", "status": "상태"}),
    }


@app.get("/export/research.xlsx")
def export_research():
    """연구 데이터를 엑셀 파일 하나(시트 5개)로 내려받는다. 의료진용."""
    today = date.today()
    with get_conn() as conn:
        tables = build_research_tables(conn, today)

    buffer = io.BytesIO()                                   # 파일 대신 메모리에 엑셀을 쓴다
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for sheet, df in tables.items():
            df.to_excel(writer, sheet_name=sheet, index=False)
            ws = writer.sheets[sheet]
            for i, col in enumerate(df.columns, start=1):   # 열 너비를 글자 수에 맞춤
                width = max([len(str(col))] + [len(str(v)) for v in df[col]]) + 4
                ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(width, 30)
    buffer.seek(0)

    filename = f"ieum_research_{today:%Y%m%d}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

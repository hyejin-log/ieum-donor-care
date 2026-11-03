import os
from datetime import date, timedelta
from typing import Literal

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

load_dotenv()                                  # .env 파일 읽기

app = FastAPI(title="이음 - Donor Care API")

# 예정일 앞뒤 60일 안에 받으면 기한 내 검사로 본다 (OPTN 공여자 추적 기준)
GRACE_DAYS = 60
# 다음 검사 알림을 보내기 시작하는 시점 (예정일 30일 전)
REMIND_DAYS = 30


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


@app.get("/health")
def health():
    """서버가 살아 있는지 확인"""
    return {"status": "ok"}


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

    for lab in recent_labs:
        items.append({"id": f"lab-{lab['lab_id']}", "level": "ok",
                      "title": "새 검사 결과가 등록됐어요",
                      "body": f"{lab['tested_at'].isoformat()} 검사 결과를 확인해 보세요."})
    return items


# ---------- 의료진: 전체 공여자 현황 ----------

@app.get("/staff/overview")
def staff_overview():
    """공여자별 상태 요약. 검사를 놓쳤거나 기록이 끊긴 공여자가 위로 온다."""
    today = date.today()
    this_week = week_start(today)
    rows = []
    with get_conn() as conn:
        donors = conn.execute(
            "SELECT donor_id, donor_code, donation_date, predonation_egfr FROM donors ORDER BY donor_code"
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
            })

    order = {"missed": 0, "window": 1, "ok": 2}
    rows.sort(key=lambda r: (order[r["status"]], -(r["weeks_since_home"] or 0)))
    return rows

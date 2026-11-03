import os
from datetime import date
from typing import Literal

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

load_dotenv()                                  # .env 파일 읽기

app = FastAPI(title="Donor Care API")


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


# ---------- 웹사이트 화면 ----------

@app.get("/")
def home():
    """웹사이트 첫 화면"""
    return FileResponse("static/index.html")


@app.get("/health")
def health():
    """서버가 살아 있는지 확인"""
    return {"status": "ok"}


# ---------- 공여자·추적검사 ----------

@app.get("/donors")
def list_donors():
    """공여자 목록"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT donor_id, donor_code, birth_year, sex, donation_date "
            "FROM donors ORDER BY donor_id"
        ).fetchall()
    return rows


@app.get("/followups/overdue")
def list_overdue():
    """기한이 지났는데 받지 않은 추적검사"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT d.donor_code, f.visit_name, f.due_date "
            "FROM followups f "
            "JOIN donors d ON d.donor_id = f.donor_id "
            "WHERE f.due_date < CURRENT_DATE "
            "AND f.completed_date IS NULL"
        ).fetchall()
    return rows


# ---------- 측정 기록 ----------

class MeasurementIn(BaseModel):
    """공여자가 입력하는 측정값. 말이 안 되는 값은 저장 전에 거절한다."""
    donor_code: str
    measured_at: date
    systolic_bp: int | None = Field(None, ge=50, le=260)
    diastolic_bp: int | None = Field(None, ge=30, le=160)
    weight_kg: float | None = Field(None, gt=20, lt=300)
    creatinine: float | None = Field(None, gt=0, lt=20)
    egfr: int | None = Field(None, ge=0, le=200)
    urine_protein: Literal["Negative", "Trace", "1+", "2+", "3+"] | None = None

    @field_validator("measured_at")
    @classmethod
    def not_in_future(cls, v: date) -> date:
        """측정 날짜는 오늘 이후일 수 없다"""
        if v > date.today():
            raise ValueError("측정 날짜는 오늘 이후일 수 없습니다")
        return v


@app.post("/measurements")
def create_measurement(m: MeasurementIn):
    """측정 기록 저장. 같은 날짜 기록이 이미 있으면 새 값으로 덮어쓴다."""
    with get_conn() as conn:
        donor = conn.execute(
            "SELECT donor_id FROM donors WHERE donor_code = %s",
            (m.donor_code,),
        ).fetchone()
        if donor is None:
            raise HTTPException(status_code=404, detail="공여자 코드를 찾을 수 없습니다")

        existing = conn.execute(
            "SELECT measurement_id FROM measurements "
            "WHERE donor_id = %s AND measured_at = %s",
            (donor["donor_id"], m.measured_at),
        ).fetchone()

        row = conn.execute(
            "INSERT INTO measurements "
            "(donor_id, measured_at, systolic_bp, diastolic_bp, weight_kg, creatinine, egfr, urine_protein) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (donor_id, measured_at) DO UPDATE SET "
            "systolic_bp = EXCLUDED.systolic_bp, "
            "diastolic_bp = EXCLUDED.diastolic_bp, "
            "weight_kg = EXCLUDED.weight_kg, "
            "creatinine = EXCLUDED.creatinine, "
            "egfr = EXCLUDED.egfr, "
            "urine_protein = EXCLUDED.urine_protein "
            "RETURNING measurement_id",
            (donor["donor_id"], m.measured_at, m.systolic_bp, m.diastolic_bp,
             m.weight_kg, m.creatinine, m.egfr, m.urine_protein),
        ).fetchone()
    return {"measurement_id": row["measurement_id"], "updated": existing is not None}


@app.get("/donors/{donor_code}/measurements")
def list_measurements(donor_code: str):
    """공여자 한 명의 측정 기록 (날짜순)"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT m.measured_at, m.systolic_bp, m.diastolic_bp, m.weight_kg, "
            "m.creatinine, m.egfr, m.urine_protein "
            "FROM measurements m "
            "JOIN donors d ON d.donor_id = m.donor_id "
            "WHERE d.donor_code = %s "
            "ORDER BY m.measured_at",
            (donor_code,),
        ).fetchall()
    return rows
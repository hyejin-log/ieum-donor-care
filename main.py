import io
from pathlib import Path
import json
import os
from datetime import date, timedelta
from typing import Literal

import pandas as pd
import agent
import auth
import ecrf
import emr
import threading
import risk
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
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
BP_LOW_SYS, BP_LOW_DIA = 90, 60           # 이보다 낮으면 저혈압 주의 (어지러움 등 증상 확인)
EGFR_LOW = 45                             # eGFR 45 미만 (CKD 3b 단계 이하)
EGFR_DROP_PERCENT = 20                    # 직전 검사보다 20% 이상 감소
PROTEIN_WARN = {"1+"}                     # 소변 단백 1+ 는 주의
PROTEIN_URGENT = {"2+", "3+"}             # 2+ 이상은 즉시 확인
WEIGHT_GAIN_PERCENT = 5                   # 최근 12주 사이 체중 5% 이상 증가


# ---------- 공여자 화면에 보여줄 기준값 설명 ----------
# 일반 성인 참고 범위다. 병원·검사 방법마다 조금씩 다르므로 판단은 의료진이 한다.
REFERENCE = {
    "egfr": {
        "name": "eGFR (사구체여과율)", "plain": "콩팥 기능 점수", "unit": "mL/min/1.73m²",
        "what": "콩팥이 1분 동안 피를 얼마나 걸러 내는지 나이·성별·크레아티닌으로 계산한 값입니다. 높을수록 콩팥이 일을 잘합니다.",
        "ranges": [["90 이상", "정상"], ["60 ~ 89", "약간 감소"], ["45 ~ 59", "경도~중등도 감소"],
                   ["30 ~ 44", "중등도~고도 감소"], ["30 미만", "고도 감소 · 신장내과 진료 필요"]],
        "donor": "신장을 하나 기증하면 남은 신장이 더 열심히 일해서, 보통 기증 전의 60~70% 정도로 회복됩니다. "
                 "그래서 기증 후 eGFR이 60대인 것은 흔한 일이고, 숫자 하나보다 시간에 따라 유지되는지가 더 중요합니다.",
    },
    "creatinine": {
        "name": "크레아티닌", "plain": "콩팥이 걸러 내는 노폐물", "unit": "mg/dL",
        "what": "근육이 움직이면 생기는 노폐물로, 콩팥이 걸러서 소변으로 내보냅니다. 콩팥 기능이 떨어지면 피 속 크레아티닌이 올라갑니다. eGFR과 반대로 움직입니다.",
        "ranges": [["약 0.7 ~ 1.3", "성인 남성 참고 범위"], ["약 0.5 ~ 1.1", "성인 여성 참고 범위"]],
        "donor": "기증 후에는 기증 전보다 조금 높아지는 것이 보통입니다. 근육량이 많으면 높게, 적으면 낮게 나올 수 있어 병원 기준과 이전 값과 함께 봅니다.",
    },
    "urine_protein": {
        "name": "소변 단백", "plain": "소변에 새어 나온 단백질", "unit": "",
        "what": "건강한 콩팥은 단백질을 소변으로 거의 내보내지 않습니다. 소변 시험지(딥스틱)로 음성·미량·1+·2+·3+로 나눕니다.",
        "ranges": [["음성", "정상"], ["미량", "정상~경도 증가 · 다시 확인"], ["1+", "양성 · 정밀검사(ACR) 필요"], ["2+ 이상", "양성 · 빨리 확인 필요"]],
        "donor": "운동·발열·탈수 뒤에는 잠깐 양성이 나올 수 있어서, 양성이면 다시 검사하거나 소변 알부민(ACR)으로 정확히 확인합니다.",
    },
    "bp": {
        "name": "혈압", "plain": "심장이 피를 밀어낼 때(위 숫자)와 쉴 때(아래 숫자) 혈관이 받는 압력", "unit": "mmHg",
        "what": "120/80 이라면 위 숫자 120이 수축기 혈압, 아래 숫자 80이 이완기 혈압입니다. 집에서 잰 혈압은 병원보다 조금 낮게 나와서 기준이 다릅니다.",
        "ranges": [["135/85 미만", "가정혈압 정상"], ["135/85 이상", "높음 · 2회 평균이 계속 높으면 알리기"],
                   ["180/110 이상", "매우 높음 · 바로 다시 재고 연락"], ["90/60 미만", "낮음 · 어지러우면 연락"]],
        "donor": "콩팥이 하나가 되면 혈압이 조금 오를 수 있어, 기증자는 혈압 관리가 가장 중요한 생활 관리입니다.",
    },
    "measure": {
        "when": ["아침: 일어나서 1시간 안에, 소변을 본 뒤, 약 먹기 전·아침 식사 전",
                 "저녁(선택): 잠들기 전",
                 "매일 같은 시간에 재면 변화를 비교하기 쉽습니다"],
        "how": ["재기 30분 전에는 커피·담배·운동을 피합니다",
                "등을 기대고 앉아 5분 쉰 뒤, 팔을 심장 높이에 둡니다",
                "1~2분 간격으로 2번 재서 둘 다 적거나 평균을 적습니다",
                "체중은 아침에 소변을 본 뒤, 식사 전, 가벼운 옷차림으로 잽니다"],
    },
}
UP_KO = {"Negative": "음성", "Trace": "미량", "1+": "1+ (양성)", "2+": "2+ (양성)", "3+": "3+ (양성)"}


def bp_assess(sys_: int | None, dia: int | None) -> dict:
    """혈압 한 번의 값을 판정한다. DB를 보지 않는 순수 함수 (저장 직후 화면 안내에 쓴다)."""
    if sys_ is None or dia is None:
        return {"level": "none", "title": "혈압 값이 없어요", "advice": ""}
    if sys_ >= BP_CRISIS_SYS or dia >= BP_CRISIS_DIA:
        return {"level": "crisis", "title": "혈압이 매우 높아요",
                "advice": "5분 쉬고 다시 재 주세요. 다시 재도 높거나 두통·가슴 통증·숨참이 있으면 바로 병원(응급실)이나 119에 연락하세요."}
    if sys_ >= BP_HOME_SYS or dia >= BP_HOME_DIA:
        return {"level": "high", "title": "혈압이 기준보다 높아요",
                "advice": f"가정혈압 기준은 {BP_HOME_SYS}/{BP_HOME_DIA} 미만이에요. 5분 쉬고 다시 재 보고, 며칠 계속 높으면 이식센터에 알려 주세요."}
    if sys_ < BP_LOW_SYS or dia < BP_LOW_DIA:
        return {"level": "low", "title": "혈압이 낮게 나왔어요",
                "advice": "앉아서 천천히 물을 마시고 다시 재 보세요. 어지럽거나 쓰러질 것 같으면 이식센터나 병원에 연락하세요."}
    return {"level": "normal", "title": "정상 범위예요",
            "advice": f"가정혈압 기준({BP_HOME_SYS}/{BP_HOME_DIA} 미만) 안에 있어요. 내일도 같은 시간에 재 주세요."}


BASE_DIR = Path(__file__).parent               # 어디서 실행해도(서버리스 포함) 파일을 찾도록 기준 폴더


def get_conn():
    """데이터베이스에 접속한다. 결과는 {칸 이름: 값} 형태로 받는다.
    배포(Neon 등)에서는 DATABASE_URL 한 줄, 내 PC에서는 DB_HOST · DB_PORT … 를 쓴다."""
    url = os.getenv("DATABASE_URL")
    if url:
        return psycopg.connect(url, row_factory=dict_row)
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
        "SELECT donor_id, donor_code, sex, donation_date, predonation_egfr FROM donors WHERE donor_code = %s",
        (donor_code,),
    ).fetchone()
    if donor is None:
        raise HTTPException(status_code=404, detail="공여자 코드를 찾을 수 없습니다")
    return donor


# ---------- 로그인 확인 ----------



CARE_SQL = """
CREATE TABLE IF NOT EXISTS chat_messages (
    message_id  SERIAL PRIMARY KEY,
    donor_id    INTEGER NOT NULL REFERENCES donors(donor_id),
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT NOT NULL,
    mode        TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_messages_donor ON chat_messages (donor_id, created_at);
CREATE TABLE IF NOT EXISTS consult_requests (
    request_id    SERIAL PRIMARY KEY,
    donor_id      INTEGER NOT NULL REFERENCES donors(donor_id),
    topic         TEXT NOT NULL,
    message       TEXT,
    contact_time  TEXT,
    status        TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'done')),
    staff_note    TEXT,
    handled_by    TEXT,
    created_at    TIMESTAMPTZ DEFAULT now(),
    handled_at    TIMESTAMPTZ
);
"""


def demo_reset_on() -> bool:
    return os.getenv("DEMO_RESET") == "1"


def load_demo_data(conn) -> None:
    """데모 데이터를 오늘 날짜 기준으로 다시 넣는다 (계정은 그대로)."""
    conn.execute((BASE_DIR / "db" / "demo_data.sql").read_text(encoding="utf-8"))


DEMO_ACCOUNTS = [("demo_donor", "donor", "데모 공여자", "D-001"), ("demo_staff", "staff", "데모 연구원", None)]


def ensure_demo_accounts(conn, password: str) -> None:
    """면접관이 바로 써 볼 수 있는 데모 계정 (없을 때만 만든다). 비밀번호는 DEMO_PASSWORD."""
    for username, role, name, code in DEMO_ACCOUNTS:
        if conn.execute("SELECT 1 FROM users WHERE username = %s", (username,)).fetchone():
            continue
        donor_id = None
        if code:
            row = conn.execute("SELECT donor_id FROM donors WHERE donor_code = %s", (code,)).fetchone()
            if row is None or conn.execute("SELECT 1 FROM users WHERE donor_id = %s", (row["donor_id"],)).fetchone():
                continue
            donor_id = row["donor_id"]
        conn.execute("INSERT INTO users (username, password_hash, role, display_name, donor_id) VALUES (%s, %s, %s, %s, %s)",
                     (username, auth.hash_password(password), role, name, donor_id))


@app.get("/guide/demo")
def demo_info():
    """로그인 화면 안내: 데모 사이트면 데모 계정 아이디를 알려 준다 (비밀번호는 README에)."""
    on = demo_reset_on()
    return {"demo": on, "accounts": [u for u, *_ in DEMO_ACCOUNTS] if on and os.getenv("DEMO_PASSWORD") else []}


@app.on_event("startup")
def prepare_database():
    """
    테이블이 없으면 만든다: 기본 4개 → 로그인 → 대화·상담 순서 (뒤의 테이블이 앞 테이블을 참조).
    배포용 데모(DEMO_RESET=1)에서 공여자가 하나도 없으면 데모 데이터도 넣는다.
    """
    try:
        with get_conn() as conn:
            conn.execute((BASE_DIR / "db" / "schema_all.sql").read_text(encoding="utf-8"))
            conn.execute(auth.AUTH_SQL)
            conn.execute(CARE_SQL)
            if demo_reset_on() and conn.execute("SELECT count(*) AS n FROM donors").fetchone()["n"] == 0:
                load_demo_data(conn)
            if demo_reset_on() and os.getenv("DEMO_PASSWORD"):
                ensure_demo_accounts(conn, os.getenv("DEMO_PASSWORD"))
    except Exception as e:                       # DB가 아직 준비되지 않아도 서버는 뜨게 한다
        print("DB 준비 실패:", type(e).__name__, e)


def current_user(request: Request) -> dict:
    """쿠키의 세션으로 로그인한 사람을 찾는다. 없으면 401."""
    with get_conn() as conn:
        user = auth.user_from_request(conn, request)
    if user is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    return user


def staff_only(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "staff":
        raise HTTPException(status_code=403, detail="의료진만 사용할 수 있습니다")
    return user


def check_access(user: dict, donor_code: str) -> None:
    """의료진은 모든 공여자, 공여자는 자기 기록만."""
    if user["role"] != "staff" and user["donor_code"] != donor_code:
        raise HTTPException(status_code=403, detail="본인 기록만 볼 수 있습니다")


def donor_access(donor_code: str, user: dict = Depends(current_user)) -> dict:
    check_access(user, donor_code)
    return user


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

    # 혈압: 가장 최근 값이 낮으면 warn (어지러움 확인)
    last_bp = [r for r in home if r["systolic_bp"] is not None and r["diastolic_bp"] is not None][-1:]
    if last_bp and bp_assess(last_bp[0]["systolic_bp"], last_bp[0]["diastolic_bp"])["level"] == "low":
        r = last_bp[0]
        alerts.append({"id": f"bp-low-{r['measured_at']}", "level": "warn", "kind": "bp",
                       "title": "혈압이 낮게 측정됨",
                       "detail": f"{r['measured_at']} · {r['systolic_bp']}/{r['diastolic_bp']} mmHg (기준 {BP_LOW_SYS}/{BP_LOW_DIA})"})

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

def page_user(request: Request) -> dict | None:
    with get_conn() as conn:
        return auth.user_from_request(conn, request)


@app.get("/")
def donor_page(request: Request):
    """공여자용 화면 (로그인 필요)"""
    if page_user(request) is None:
        return RedirectResponse("/login")
    return FileResponse(BASE_DIR / "static/donor.html")


@app.get("/staff")
def staff_page(request: Request):
    """의료진용 화면: 공여자 목록과 검사 결과 입력"""
    u = page_user(request)
    if u is None or u["role"] != "staff":
        return RedirectResponse("/login")
    return FileResponse(BASE_DIR / "static/staff.html")


@app.get("/dashboard")
def dashboard_page(request: Request):
    """의료진용 공여자 상세 화면"""
    u = page_user(request)
    if u is None or u["role"] != "staff":
        return RedirectResponse("/login")
    return FileResponse(BASE_DIR / "static/dashboard.html")


@app.get("/login")
def login_page():
    return FileResponse(BASE_DIR / "static/login.html")


# ---------- 로그인 · 회원가입 ----------

class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=30)
    password: str = Field(min_length=1, max_length=100)


class SignupIn(BaseModel):
    role: Literal["donor", "staff"]
    username: str = Field(pattern=r"^[a-z0-9_]{4,30}$")          # 영어 소문자·숫자·_ 4~30자
    password: str = Field(min_length=8, max_length=100)
    display_name: str = Field(min_length=1, max_length=30)
    donor_code: str | None = None                                 # 공여자 가입: 병원에서 받은 코드
    birth_year: int | None = None                                 # 공여자 가입: 본인 확인용
    invite_code: str | None = None                                # 의료진 가입: 병원 가입 코드

    @field_validator("password")
    @classmethod
    def strong_enough(cls, v: str) -> str:
        if v.isdigit() or v.isalpha():
            raise ValueError("비밀번호는 글자와 숫자를 섞어 8자 이상으로 만들어 주세요")
        return v


def _login_response(conn, request: Request, user: dict) -> Response:
    token = auth.create_session(conn, user["user_id"])
    body = {"role": user["role"], "next": "/staff" if user["role"] == "staff" else "/"}
    if request.headers.get("x-client") == "mobile":       # 휴대폰 앱: 토큰을 받아 기기 보안 저장소에 둔다
        body["token"] = token
    res = Response(content=json.dumps(body), media_type="application/json")
    auth.set_cookie(res, token, secure=request.url.scheme == "https")
    return res


@app.post("/auth/signup")
def signup(body: SignupIn, request: Request):
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username = %s", (body.username,)).fetchone():
            raise HTTPException(status_code=409, detail="이미 쓰고 있는 아이디예요")
        donor_id = None
        if body.role == "donor":
            donor = conn.execute(
                "SELECT donor_id, birth_year FROM donors WHERE donor_code = %s", (body.donor_code or "",)
            ).fetchone()
            # 코드가 없는지, 연도가 틀렸는지 구분해서 알려 주지 않는다 (남의 코드 추측 방지)
            if donor is None or donor["birth_year"] != body.birth_year:
                raise HTTPException(status_code=400, detail="공여자 코드나 출생 연도가 맞지 않아요. 이식센터에 확인해 주세요")
            if conn.execute("SELECT 1 FROM users WHERE donor_id = %s", (donor["donor_id"],)).fetchone():
                raise HTTPException(status_code=409, detail="이 공여자 코드로 이미 가입한 계정이 있어요")
            donor_id = donor["donor_id"]
        elif not auth.staff_invite_ok(body.invite_code or ""):
            raise HTTPException(status_code=403, detail="의료진 가입 코드가 맞지 않아요 (.env 의 STAFF_INVITE_CODE)")
        user = conn.execute(
            "INSERT INTO users (username, password_hash, role, display_name, donor_id) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING user_id, role",
            (body.username, auth.hash_password(body.password), body.role, body.display_name, donor_id),
        ).fetchone()
        return _login_response(conn, request, user)


@app.post("/auth/login")
def login(body: LoginIn, request: Request):
    with get_conn() as conn:
        auth.check_rate(conn, body.username)
        user = conn.execute(
            "SELECT user_id, role, password_hash FROM users WHERE username = %s", (body.username,)
        ).fetchone()
        if user is None or not auth.verify_password(body.password, user["password_hash"]):
            auth.record_fail(conn, body.username)
            conn.commit()                                # 401을 보내기 전에 실패 기록을 확정한다
            raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 맞지 않아요")
        return _login_response(conn, request, user)


@app.post("/auth/logout")
def logout(request: Request):
    with get_conn() as conn:
        auth.delete_session(conn, auth.request_token(request))
    res = Response(content='{"ok": true}', media_type="application/json")
    res.delete_cookie(auth.COOKIE)
    return res


@app.get("/auth/me")
def me(user: dict = Depends(current_user)):
    return {k: user[k] for k in ("username", "role", "display_name", "donor_code")}


def _key_check(key: str | None) -> dict | None:
    """키 값 대신 모양만 확인한다: Groq 키는 gsk_ 로 시작한다."""
    if not key:
        return None
    k = key.strip().strip('"\'').strip()
    return {"length": len(k), "starts_with_gsk_": k.startswith("gsk_"),
            "has_space_or_quote": any(c in k for c in " '\"<>")}


@app.get("/health")
def health():
    """서버가 살아 있는지 확인. 배포 환경에서는 키 정보 등은 보여 주지 않는다."""
    if os.getenv("VERCEL"):
        return {"status": "ok", "llm_provider": agent.LLM_PROVIDER}
    return {"status": "ok", "llm_provider": agent.LLM_PROVIDER,
            "llm_model": agent.GROQ_MODEL if agent.LLM_PROVIDER == "groq" else agent.OLLAMA_MODEL,
            "groq_key_set": bool(os.getenv("GROQ_API_KEY")),          # 키 값은 보여주지 않는다
            "groq_key_check": _key_check(os.getenv("GROQ_API_KEY")),
            "llm_last_error": agent.LAST_LLM_ERROR}


@app.get("/health/groq-models", dependencies=[Depends(staff_only)])
def groq_model_list():
    """이 키로 쓸 수 있는 Groq 모델 목록 (모델 이름만)"""
    try:
        return {"current": agent.GROQ_MODEL, "available": agent.groq_models()}
    except Exception as e:
        return {"current": agent.GROQ_MODEL, "error": type(e).__name__}


# ---------- 공여자·추적검사 ----------

@app.get("/donors", dependencies=[Depends(staff_only)])
def list_donors():
    """공여자 목록"""
    with get_conn() as conn:
        return conn.execute(
            "SELECT donor_id, donor_code, birth_year, sex, donation_date, predonation_egfr "
            "FROM donors ORDER BY donor_code"
        ).fetchall()


@app.get("/followups/overdue", dependencies=[Depends(staff_only)])
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


@app.get("/donors/{donor_code}/summary", dependencies=[Depends(donor_access)])
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
        "sex": donor["sex"],
        "donation_date": donor["donation_date"],
        "days_since_donation": (today - donor["donation_date"]).days,
        "predonation_egfr": donor["predonation_egfr"],
        "latest_egfr": latest["egfr"] if latest else None,
        "latest_egfr_date": latest["tested_at"] if latest else None,
        "egfr_percent_of_predonation": percent,
        "visits": visits,
    }


@app.get("/guide/reference")
def reference():
    """검사 수치·혈압 기준값과 쉬운 설명 (공여자 화면과 안내 Agent가 같은 내용을 쓴다)"""
    return {**REFERENCE, "thresholds": {
        "bp_home": [BP_HOME_SYS, BP_HOME_DIA], "bp_crisis": [BP_CRISIS_SYS, BP_CRISIS_DIA],
        "bp_low": [BP_LOW_SYS, BP_LOW_DIA], "egfr_low": EGFR_LOW, "grace_days": GRACE_DAYS}}


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
def create_home(m: HomeIn, user: dict = Depends(current_user)):
    """자가 기록 저장. 같은 날짜 기록이 있으면 새 값으로 덮어쓴다."""
    check_access(user, m.donor_code)
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
    return {"home_id": row["home_id"], "updated": existing is not None,
            "bp": bp_assess(m.systolic_bp, m.diastolic_bp)}


@app.get("/donors/{donor_code}/home", dependencies=[Depends(donor_access)])
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
    entered_by: str | None = Field(None, max_length=40)        # 무시: 로그인한 의료진 이름으로 저장

    @field_validator("tested_at")
    @classmethod
    def not_in_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("검사 날짜는 오늘 이후일 수 없습니다")
        return v


def save_lab(conn, donor: dict, tested_at: date, creatinine, egfr, urine_protein, entered_by: str) -> dict:
    """
    검사 결과 1건 저장 (화면 입력과 EMR 자동 가져오기가 같이 쓴다). 같은 날짜 결과가 있으면 덮어쓴다.
    아직 완료되지 않은 추적검사 중 검사 기간이 시작된 가장 이른 것을 이 결과로 '완료' 처리한다.
    status: new(새로 저장) / updated(값이 바뀜) / same(이미 같은 값이 있어 건너뜀)
    """
    old = conn.execute(
        "SELECT creatinine, egfr, urine_protein FROM lab_results WHERE donor_id = %s AND tested_at = %s",
        (donor["donor_id"], tested_at),
    ).fetchone()
    if old and (float(old["creatinine"]) if old["creatinine"] is not None else None) == creatinine \
            and old["egfr"] == egfr and old["urine_protein"] == urine_protein:
        return {"lab_id": None, "completed_visit": None, "status": "same"}

    visit = conn.execute(
        "SELECT followup_id, visit_name FROM followups "
        "WHERE donor_id = %s AND completed_date IS NULL AND due_date - %s <= %s "
        "ORDER BY due_date LIMIT 1",
        (donor["donor_id"], GRACE_DAYS, tested_at),
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
        (donor["donor_id"], tested_at, creatinine, egfr, urine_protein, followup_id, entered_by),
    ).fetchone()

    completed = None
    if visit and row["followup_id"] == followup_id:
        conn.execute("UPDATE followups SET completed_date = %s WHERE followup_id = %s",
                     (tested_at, followup_id))
        completed = visit["visit_name"]
    return {"lab_id": row["lab_id"], "completed_visit": completed, "status": "updated" if old else "new"}


@app.post("/labs")
def create_lab(lab: LabIn, user: dict = Depends(staff_only)):
    """병원 검사 결과 입력 (의료진). 입력자는 로그인한 의료진 이름으로 남긴다."""
    with get_conn() as conn:
        donor = get_donor(conn, lab.donor_code)
        r = save_lab(conn, donor, lab.tested_at, lab.creatinine, lab.egfr, lab.urine_protein, user["display_name"])
    return {"lab_id": r["lab_id"], "completed_visit": r["completed_visit"]}


# ---------- EMR 연동 (FHIR) ----------

@app.get("/mock-emr/fhir/Observation")
def mock_emr_observations(request: Request):
    """모의 EMR의 FHIR 검색 API. 예: ?patient.identifier=urn:ieum:donor-code|D-001 (가상 자료)"""
    code = request.query_params.get("patient.identifier", "").split("|")[-1]
    with get_conn() as conn:                              # 모의 EMR은 기증일을 기준으로 가상 결과를 만든다
        row = conn.execute("SELECT donation_date FROM donors WHERE donor_code = %s", (code,)).fetchone()
    return emr.mock_bundle(code, row["donation_date"] if row else None)


def sync_emr(donor_code: str | None = None) -> list[dict]:
    """EMR에서 검사 결과를 받아 이음에 저장한다. 공여자별로 새로 저장·건너뜀·문제를 돌려준다."""
    report = []
    with get_conn() as conn:
        codes = [donor_code] if donor_code else [
            r["donor_code"] for r in conn.execute("SELECT donor_code FROM donors ORDER BY donor_code").fetchall()]
    for code in codes:
        item = {"donor_code": code, "new": 0, "updated": 0, "same": 0, "completed_visits": [], "problems": []}
        try:
            with get_conn() as conn:                     # 공여자 한 명 단위로 저장 (중간에 실패해도 앞 사람은 유지)
                donor = get_donor(conn, code)
                when = conn.execute("SELECT donation_date FROM donors WHERE donor_id = %s",
                                    (donor["donor_id"],)).fetchone()["donation_date"]
                rows, item["problems"] = emr.parse_bundle(emr.fetch_bundle(code, when))
                for r in rows:
                    res = save_lab(conn, donor, r["tested_at"], r["creatinine"], r["egfr"], r["urine_protein"],
                                   "EMR 자동(FHIR)")
                    item[res["status"]] += 1
                    if res["completed_visit"]:
                        item["completed_visits"].append(res["completed_visit"])
        except Exception as e:
            item["problems"].append(f"가져오기 실패: {type(e).__name__}")
        report.append(item)
    return report


@app.post("/emr/sync", dependencies=[Depends(staff_only)])
def emr_sync(donor_code: str | None = None):
    """의료진이 버튼으로 EMR 검사 결과를 가져온다. 같은 결과는 다시 저장하지 않는다."""
    return {"source": os.getenv("EMR_FHIR_BASE") or "모의 EMR (가상 자료)", "results": sync_emr(donor_code)}


@app.on_event("startup")
def start_emr_auto_sync():
    """EMR_SYNC_MINUTES 를 넣으면 그 간격마다 자동으로 가져온다 (0 또는 비어 있으면 끔)."""
    minutes = int(os.getenv("EMR_SYNC_MINUTES") or 0)
    if minutes <= 0:
        return

    def loop():
        try:
            sync_emr()
        except Exception as e:
            print("EMR 자동 가져오기 실패:", type(e).__name__)
        t = threading.Timer(minutes * 60, loop)
        t.daemon = True
        t.start()

    loop()


@app.get("/donors/{donor_code}/labs", dependencies=[Depends(donor_access)])
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


@app.get("/donors/{donor_code}/alerts", dependencies=[Depends(staff_only)])
def list_alerts(donor_code: str):
    """의료진용 경고 목록 (혈압 · 체중 · 검사 결과 규칙)"""
    with get_conn() as conn:
        donor = get_donor(conn, donor_code)
        return load_alerts(conn, donor["donor_id"], date.today())


# ---------- 공여자 알림 ----------

@app.get("/donors/{donor_code}/notifications", dependencies=[Depends(donor_access)])
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

    # 매일 1회 기록: 오늘 기록이 없으면 알림 (id에 날짜를 넣어 하루에 한 번만 새 알림)
    if last_home is None or last_home < today:
        days = None if last_home is None else (today - last_home).days
        items.append({"id": f"daily-{today.isoformat()}", "level": "info",
                      "title": "오늘 혈압·체중을 기록해 주세요",
                      "body": "아직 기록이 없어요." if days is None
                              else ("마지막 기록은 어제예요." if days == 1 else f"마지막 기록은 {days}일 전이에요.")})

    # 혈압 경고만 공여자에게도 보여준다. 검사 결과 해석은 의료진이 전달한다.
    for a in bp_alerts:
        if a["id"].startswith("bp-low-"):
            items.append({"id": a["id"], "level": "warn", "title": "혈압이 낮게 측정되었어요",
                          "body": "어지럽거나 쓰러질 것 같으면 이식센터나 병원에 연락해 주세요. 물을 마시고 앉아서 다시 재 보세요."})
        elif a["level"] == "urgent":
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


def save_chat(donor_code: str, question: str, reply: str, mode: str | None) -> None:
    """질문과 답을 대화 기록에 남긴다. 기록 저장이 실패해도 답변은 그대로 보낸다."""
    try:
        with get_conn() as conn:
            donor = get_donor(conn, donor_code)
            conn.execute(
                "INSERT INTO chat_messages (donor_id, role, content, mode) VALUES (%s, 'user', %s, NULL), (%s, 'assistant', %s, %s)",
                (donor["donor_id"], question, donor["donor_id"], reply, mode),
            )
    except Exception as e:
        print("대화 기록 저장 실패:", type(e).__name__)


@app.get("/donors/{donor_code}/chat/history", dependencies=[Depends(donor_access)])
def chat_history(donor_code: str, limit: int = 40):
    """최근 대화 기록 (오래된 것 → 최근 순). 공여자 본인과 의료진만 볼 수 있다."""
    limit = max(1, min(limit, 200))
    with get_conn() as conn:
        donor = get_donor(conn, donor_code)
        rows = conn.execute(
            "SELECT role, content, mode, created_at FROM chat_messages WHERE donor_id = %s "
            "ORDER BY created_at DESC, message_id DESC LIMIT %s",
            (donor["donor_id"], limit),
        ).fetchall()
    return list(reversed(rows))


@app.delete("/donors/{donor_code}/chat/history", dependencies=[Depends(donor_access)])
def clear_chat_history(donor_code: str):
    """공여자가 자기 대화 기록을 지운다."""
    with get_conn() as conn:
        donor = get_donor(conn, donor_code)
        n = conn.execute("DELETE FROM chat_messages WHERE donor_id = %s", (donor["donor_id"],)).rowcount
    return {"deleted": n}


@app.post("/donors/{donor_code}/chat", dependencies=[Depends(donor_access)])
def donor_chat(donor_code: str, chat: ChatIn):
    """공여자 질문에 안내 Agent가 답한다. 도구는 이 공여자의 기록만 조회하도록 고정한다."""
    with get_conn() as conn:
        get_donor(conn, donor_code)                     # 없는 공여자면 404
    if chat.messages[-1].role != "user":
        raise HTTPException(status_code=422, detail="마지막 메시지는 질문이어야 합니다")

    impl = chat_tools(donor_code)
    result = agent.run_agent([m.model_dump() for m in chat.messages], impl)
    result["disclaimer"] = agent.DISCLAIMER
    save_chat(donor_code, chat.messages[-1].content, result.get("reply", ""), result.get("mode"))
    return result


@app.post("/donors/{donor_code}/chat/stream", dependencies=[Depends(donor_access)])
def donor_chat_stream(donor_code: str, chat: ChatIn):
    """donor_chat과 같지만, 답을 만들어지는 대로 한 줄씩(NDJSON) 보낸다."""
    with get_conn() as conn:
        get_donor(conn, donor_code)
    if chat.messages[-1].role != "user":
        raise HTTPException(status_code=422, detail="마지막 메시지는 질문이어야 합니다")
    impl = chat_tools(donor_code)
    messages = [m.model_dump() for m in chat.messages]

    def lines():
        text, mode = "", None
        for event in agent.run_agent_stream(messages, impl):
            if event.get("type") == "meta":
                mode = event.get("mode")
            elif event.get("type") == "delta":
                text += event.get("text", "")
            elif event.get("type") == "replace":
                text, mode = event.get("text", ""), event.get("mode", mode)
            yield json.dumps(event, ensure_ascii=False, default=str) + "\n"
        if text:                                          # 답을 다 보낸 뒤 기록에 남긴다
            save_chat(donor_code, messages[-1]["content"], text, mode)

    return StreamingResponse(lines(), media_type="application/x-ndjson")


@app.post("/donors/{donor_code}/chat/warmup", dependencies=[Depends(donor_access)])
def agent_warmup(donor_code: str):
    """대화 창을 열 때 호출: 로컬 모델에 이 공여자의 자료를 미리 읽혀 둔다."""
    with get_conn() as conn:
        get_donor(conn, donor_code)
    return {"ready": agent.warm_up(chat_tools(donor_code))}


# ---------- 상담사 연결 ----------
# 실시간 상담은 사람이 대기해야 하므로, 데모에서는 '상담 요청 → 담당자가 전화' 방식으로 만든다.
CONSULT_TOPICS = {
    "test": "검사 · 일정 (이식 코디네이터)",
    "health": "몸 상태 · 약 (이식 코디네이터 · 간호사)",
    "mind": "마음 · 생활 (사회복지사 · 심리 상담)",
}


@app.get("/guide/contact")
def contact_info():
    """이식센터 연락처 (.env 의 CENTER_PHONE · CENTER_HOURS, 없으면 데모 안내)"""
    return {"phone": os.getenv("CENTER_PHONE") or "", "hours": os.getenv("CENTER_HOURS") or "평일 09:00 ~ 17:00",
            "topics": CONSULT_TOPICS}


class ConsultIn(BaseModel):
    topic: Literal["test", "health", "mind"]
    message: str | None = Field(None, max_length=500)
    contact_time: Literal["오전", "오후", "언제든"] = "언제든"


@app.post("/donors/{donor_code}/consult", dependencies=[Depends(donor_access)])
def create_consult(donor_code: str, body: ConsultIn):
    """공여자가 상담을 요청한다. 같은 주제로 처리되지 않은 요청이 있으면 새로 만들지 않고 내용을 덧붙인다."""
    with get_conn() as conn:
        donor = get_donor(conn, donor_code)
        open_req = conn.execute(
            "SELECT request_id FROM consult_requests WHERE donor_id = %s AND topic = %s AND status = 'new'",
            (donor["donor_id"], body.topic),
        ).fetchone()
        if open_req:
            conn.execute(
                "UPDATE consult_requests SET message = concat_ws(E'\\n', message, %s::text), contact_time = %s, created_at = now() "
                "WHERE request_id = %s",
                (body.message, body.contact_time, open_req["request_id"]),
            )
            return {"request_id": open_req["request_id"], "updated": True}
        row = conn.execute(
            "INSERT INTO consult_requests (donor_id, topic, message, contact_time) VALUES (%s, %s, %s, %s) RETURNING request_id",
            (donor["donor_id"], body.topic, body.message, body.contact_time),
        ).fetchone()
    return {"request_id": row["request_id"], "updated": False}


@app.get("/donors/{donor_code}/consult", dependencies=[Depends(donor_access)])
def list_my_consults(donor_code: str):
    """내 상담 요청과 처리 상태 (최근 10건)"""
    with get_conn() as conn:
        donor = get_donor(conn, donor_code)
        rows = conn.execute(
            "SELECT request_id, topic, message, contact_time, status, staff_note, created_at, handled_at "
            "FROM consult_requests WHERE donor_id = %s ORDER BY created_at DESC LIMIT 10",
            (donor["donor_id"],),
        ).fetchall()
    for r in rows:
        r["topic_label"] = CONSULT_TOPICS.get(r["topic"], r["topic"])
    return rows


@app.get("/staff/consults", dependencies=[Depends(staff_only)])
def staff_consults(status: Literal["new", "done", "all"] = "new"):
    """의료진: 상담 요청 목록. 처리 전 요청이 먼저, 오래 기다린 순으로."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT c.request_id, d.donor_code, c.topic, c.message, c.contact_time, c.status, c.staff_note, "
            "c.handled_by, c.created_at, c.handled_at FROM consult_requests c JOIN donors d ON d.donor_id = c.donor_id "
            "WHERE %s::text = 'all' OR c.status = %s::text ORDER BY (c.status = 'done'), c.created_at LIMIT 100",
            (status, status),
        ).fetchall()
    for r in rows:
        r["topic_label"] = CONSULT_TOPICS.get(r["topic"], r["topic"])
    return rows


class ConsultDoneIn(BaseModel):
    note: str | None = Field(None, max_length=500)


@app.post("/staff/consults/{request_id}/done")
def finish_consult(request_id: int, body: ConsultDoneIn, user: dict = Depends(staff_only)):
    """의료진: 상담 완료 처리. 누가 언제 처리했는지 남긴다."""
    with get_conn() as conn:
        row = conn.execute(
            "UPDATE consult_requests SET status = 'done', staff_note = %s, handled_by = %s, handled_at = now() "
            "WHERE request_id = %s RETURNING request_id",
            (body.note, user["display_name"], request_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="상담 요청을 찾을 수 없습니다")
    return {"ok": True}


# ---------- 매일 자동 작업 (Vercel Cron) ----------

@app.get("/cron/daily")
def cron_daily(request: Request):
    """
    Vercel Cron 이 하루 한 번 부른다. 머리글 Authorization: Bearer <CRON_SECRET> 가 맞을 때만 실행.
    1) 데모 초기화(DEMO_RESET=1): 시연하며 바뀐 기록을 오늘 날짜 기준 데모로 되돌림
    2) EMR(FHIR) 검사 결과 가져오기
    """
    secret = os.getenv("CRON_SECRET")
    if not secret or request.headers.get("authorization") != f"Bearer {secret}":
        raise HTTPException(status_code=401, detail="unauthorized")
    done = {}
    if demo_reset_on():
        with get_conn() as conn:
            load_demo_data(conn)
        done["demo_reset"] = True
    else:
        done["emr"] = sync_emr()                 # 데모 초기화 때는 EMR 결과를 일부러 남겨 두지 않는다(시연용)
    return done


# ---------- 의료진: 전체 공여자 현황 ----------

@app.get("/staff/overview", dependencies=[Depends(staff_only)])
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
    """공여자 요약 · 검사 결과 · 자가 기록 · 주간 기록 · 추적검사 표 5개 + eCRF 표 2개를 만든다."""
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

    ecrf_table = ecrf.ecrf_rows(donors, visits, labs, home)     # eCRF(REDCap) 가져오기 형식

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
        "eCRF 입력용": ecrf_table,
        "eCRF 변수정의": ecrf.DICTIONARY,
    }


@app.get("/export/research.xlsx", dependencies=[Depends(staff_only)])
def export_research():
    """연구 데이터를 엑셀 파일 하나(시트 7개)로 내려받는다. 의료진용."""
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


@app.get("/export/ecrf.csv", dependencies=[Depends(staff_only)])
def export_ecrf(kind: Literal["data", "dictionary"] = "data"):
    """
    eCRF에 바로 올릴 수 있는 CSV. kind=data: 입력용(REDCap Import), kind=dictionary: 변수 정의서.
    엑셀에서 한글이 깨지지 않도록 UTF-8 BOM(utf-8-sig)으로 저장한다.
    """
    today = date.today()
    with get_conn() as conn:
        tables = build_research_tables(conn, today)
    df = tables["eCRF 입력용"] if kind == "data" else tables["eCRF 변수정의"]
    body = df.to_csv(index=False).encode("utf-8-sig")
    name = f"ieum_ecrf_{'data' if kind == 'data' else 'dictionary'}_{today:%Y%m%d}.csv"
    return Response(content=body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})

"""
로그인 · 회원가입 · 세션.

- 비밀번호는 원문을 저장하지 않고 PBKDF2-SHA256(소금 + 20만 번 반복)으로 만든 해시만 저장한다.
- 로그인하면 임의의 세션 토큰을 쿠키(HttpOnly)로 준다. DB에는 토큰 원문이 아니라 SHA-256 해시를 저장한다.
- 공여자 가입: 병원이 알려 준 '공여자 코드 + 출생 연도'가 맞아야 그 공여자 계정을 만들 수 있다.
- 의료진 가입: .env 의 STAFF_INVITE_CODE(병원이 정한 가입 코드)를 알아야 한다. 설정이 없으면 의료진 가입은 닫힌다.
- 같은 아이디로 10분 안에 5번 틀리면 잠시 로그인을 막는다(무작위 대입 공격 방지).
"""
import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

from dotenv import dotenv_values
from fastapi import HTTPException, Request

COOKIE = "ieum_session"
SESSION_DAYS = 14
PBKDF2_ROUNDS = 200_000
MAX_FAILS, FAIL_WINDOW_SEC = 5, 600

AUTH_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id       SERIAL PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('donor', 'staff')),
    display_name  TEXT NOT NULL,
    donor_id      INTEGER UNIQUE REFERENCES donors(donor_id),
    created_at    TIMESTAMP DEFAULT now(),
    CONSTRAINT donor_has_donor_id CHECK (role <> 'donor' OR donor_id IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    expires_at  TIMESTAMPTZ NOT NULL
);
"""

_fails: dict[str, list[float]] = {}          # 아이디 → 최근 실패 시각들 (서버 메모리)


# ---------- 비밀번호 ----------

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS).hex()
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt, digest = stored.split("$")
    except ValueError:
        return False
    test = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds)).hex()
    return hmac.compare_digest(test, digest)              # 걸린 시간으로 답을 추측하지 못하게 비교


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- 로그인 시도 제한 ----------

def check_rate(username: str) -> None:
    now = time.time()
    recent = [t for t in _fails.get(username, []) if now - t < FAIL_WINDOW_SEC]
    _fails[username] = recent
    if len(recent) >= MAX_FAILS:
        raise HTTPException(status_code=429, detail="로그인을 여러 번 실패했어요. 10분 뒤에 다시 시도해 주세요.")


def record_fail(username: str) -> None:
    _fails.setdefault(username, []).append(time.time())


# ---------- 세션 ----------

def create_session(conn, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute("DELETE FROM sessions WHERE expires_at < now()")          # 만료된 세션 정리
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
        (_token_hash(token), user_id, datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)),
    )
    return token


def delete_session(conn, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = %s", (_token_hash(token),))


def request_token(request: Request) -> str | None:
    """웹은 쿠키, 휴대폰 앱은 'Authorization: Bearer 토큰' 머리글로 세션 토큰을 보낸다."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return request.cookies.get(COOKIE)


def user_from_request(conn, request: Request) -> dict | None:
    token = request_token(request)
    if not token:
        return None
    return conn.execute(
        "SELECT u.user_id, u.username, u.role, u.display_name, d.donor_code "
        "FROM sessions s JOIN users u ON u.user_id = s.user_id "
        "LEFT JOIN donors d ON d.donor_id = u.donor_id "
        "WHERE s.token_hash = %s AND s.expires_at > now()",
        (_token_hash(token),),
    ).fetchone()


def staff_invite_ok(code: str) -> bool:
    # .env 를 매번 다시 읽는다: 서버를 다시 켜지 않아도 바꾼 가입 코드가 바로 적용되게
    expected = (dotenv_values(".env").get("STAFF_INVITE_CODE") or os.getenv("STAFF_INVITE_CODE") or "")
    expected = expected.strip().strip('"\'').strip()
    return bool(expected) and hmac.compare_digest(code.strip(), expected)


def set_cookie(response, token: str, secure: bool) -> None:
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=secure)

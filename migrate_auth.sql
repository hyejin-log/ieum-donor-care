-- 이음: 로그인용 테이블 (서버가 시작할 때 자동으로도 만듭니다)
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

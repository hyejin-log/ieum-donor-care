-- 이음: 대화 기록 · 상담 요청 테이블 (서버가 시작할 때 자동으로도 만듭니다)
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

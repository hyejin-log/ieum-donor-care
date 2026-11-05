-- 이음: 전체 테이블 (없을 때만 만든다 · 여러 번 실행해도 안전)
CREATE TABLE IF NOT EXISTS donors (
    donor_id         SERIAL PRIMARY KEY,
    donor_code       TEXT NOT NULL UNIQUE,
    birth_year       INTEGER,
    sex              TEXT,
    donation_date    DATE NOT NULL,
    predonation_egfr INTEGER,
    created_at       TIMESTAMP DEFAULT now()
);
CREATE TABLE IF NOT EXISTS followups (
    followup_id     SERIAL PRIMARY KEY,
    donor_id        INTEGER NOT NULL REFERENCES donors(donor_id),
    visit_name      TEXT NOT NULL,
    due_date        DATE NOT NULL,
    completed_date  DATE
);
CREATE TABLE IF NOT EXISTS home_measurements (
    home_id       SERIAL PRIMARY KEY,
    donor_id      INTEGER NOT NULL REFERENCES donors(donor_id),
    measured_at   DATE NOT NULL,
    systolic_bp   INTEGER,
    diastolic_bp  INTEGER,
    weight_kg     NUMERIC(5,2),
    created_at    TIMESTAMP DEFAULT now(),
    CONSTRAINT uq_home_day UNIQUE (donor_id, measured_at)
);
CREATE TABLE IF NOT EXISTS lab_results (
    lab_id        SERIAL PRIMARY KEY,
    donor_id      INTEGER NOT NULL REFERENCES donors(donor_id),
    tested_at     DATE NOT NULL,
    creatinine    NUMERIC(5,2),
    egfr          INTEGER,
    urine_protein TEXT,
    followup_id   INTEGER REFERENCES followups(followup_id),
    entered_by    TEXT NOT NULL,
    created_at    TIMESTAMP DEFAULT now(),
    CONSTRAINT uq_lab_day UNIQUE (donor_id, tested_at)
);

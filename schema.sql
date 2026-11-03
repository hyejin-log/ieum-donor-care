-- 생체 신장 공여자 추적관리 앱 데이터베이스 구조

-- 공여자 (실명 대신 공여자 코드 사용)
CREATE TABLE donors (
    donor_id      SERIAL PRIMARY KEY,
    donor_code    TEXT NOT NULL UNIQUE,
    birth_year    INTEGER,
    sex           TEXT,
    donation_date DATE NOT NULL,
    created_at    TIMESTAMP DEFAULT now()
);

-- 측정 기록 (공여자 1명당 여러 건)
CREATE TABLE measurements (
    measurement_id  SERIAL PRIMARY KEY,
    donor_id        INTEGER NOT NULL REFERENCES donors(donor_id),
    measured_at     DATE NOT NULL,
    systolic_bp     INTEGER,
    diastolic_bp    INTEGER,
    weight_kg       NUMERIC(5,2),
    creatinine      NUMERIC(5,2),
    egfr            INTEGER,
    urine_protein   TEXT
);

-- 추적검사 일정 (completed_date가 비어 있고 due_date가 지났으면 검사 누락)
CREATE TABLE followups (
    followup_id     SERIAL PRIMARY KEY,
    donor_id        INTEGER NOT NULL REFERENCES donors(donor_id),
    visit_name      TEXT NOT NULL,
    due_date        DATE NOT NULL,
    completed_date  DATE
);
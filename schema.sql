-- 이음: 생체 신장 공여자 추적관리 앱 데이터베이스 구조
-- 연구용 설정: 공여자는 집에서 잰 혈압·체중을 주 1회 기록하고,
--              병원 검사 결과는 연구 담당자(CRC·코디네이터)가 입력한다.

-- 공여자 (실명 대신 공여자 코드 사용)
CREATE TABLE donors (
    donor_id         SERIAL PRIMARY KEY,
    donor_code       TEXT NOT NULL UNIQUE,
    birth_year       INTEGER,
    sex              TEXT,
    donation_date    DATE NOT NULL,
    predonation_egfr INTEGER,          -- 기증 전 eGFR (나의 기준값)
    created_at       TIMESTAMP DEFAULT now()
);

-- 추적검사 일정 (예정일 + 60일이 지나도 completed_date가 비어 있으면 검사 놓침)
CREATE TABLE followups (
    followup_id     SERIAL PRIMARY KEY,
    donor_id        INTEGER NOT NULL REFERENCES donors(donor_id),
    visit_name      TEXT NOT NULL,
    due_date        DATE NOT NULL,
    completed_date  DATE
);

-- 공여자 자가 기록: 집에서 잰 혈압·체중 (하루 1건, 같은 날 다시 입력하면 덮어씀)
CREATE TABLE home_measurements (
    home_id       SERIAL PRIMARY KEY,
    donor_id      INTEGER NOT NULL REFERENCES donors(donor_id),
    measured_at   DATE NOT NULL,
    systolic_bp   INTEGER,
    diastolic_bp  INTEGER,
    weight_kg     NUMERIC(5,2),
    created_at    TIMESTAMP DEFAULT now(),
    CONSTRAINT uq_home_day UNIQUE (donor_id, measured_at)
);

-- 병원 검사 결과: 연구 담당자가 입력, 해당 추적검사와 연결
CREATE TABLE lab_results (
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

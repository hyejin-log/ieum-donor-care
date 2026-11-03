-- 이음: 측정 테이블을 "공여자 자가 기록"과 "병원 검사 결과"로 나누기
-- 한 번만 실행합니다. 중간에 오류가 나면 아무것도 바뀌지 않습니다(BEGIN ~ COMMIT).

BEGIN;

-- 1) 공여자가 집에서 재는 값: 혈압, 체중 (주 1회 권장, 하루 1건)
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

-- 2) 병원 검사 결과: 연구 담당자(CRC·코디네이터)가 입력
CREATE TABLE lab_results (
    lab_id        SERIAL PRIMARY KEY,
    donor_id      INTEGER NOT NULL REFERENCES donors(donor_id),
    tested_at     DATE NOT NULL,
    creatinine    NUMERIC(5,2),
    egfr          INTEGER,
    urine_protein TEXT,
    followup_id   INTEGER REFERENCES followups(followup_id),  -- 어느 추적검사의 결과인지
    entered_by    TEXT NOT NULL,                              -- 입력한 사람
    created_at    TIMESTAMP DEFAULT now(),
    CONSTRAINT uq_lab_day UNIQUE (donor_id, tested_at)
);

-- 3) 기존 기록 중 혈압·체중만 옮김
--    (예전에 연습으로 넣은 크레아티닌·eGFR·소변 단백은 병원 결과가 아니므로 옮기지 않음)
INSERT INTO home_measurements (donor_id, measured_at, systolic_bp, diastolic_bp, weight_kg)
SELECT donor_id, measured_at, systolic_bp, diastolic_bp, weight_kg
FROM measurements
WHERE systolic_bp IS NOT NULL OR weight_kg IS NOT NULL
ON CONFLICT (donor_id, measured_at) DO NOTHING;

-- 4) 예전 테이블 삭제
DROP TABLE measurements;

COMMIT;

SELECT 'home_measurements' AS 테이블, count(*) AS 건수 FROM home_measurements
UNION ALL
SELECT 'lab_results', count(*) FROM lab_results;

-- 이음: 데모 데이터 (모두 가상 값)
-- 여러 번 실행해도 같은 기록은 다시 들어가지 않습니다.
--
-- 공여자 세 명이 서로 다른 상황이 되도록 만듭니다.
--   D-001  1년 검사를 놓침, 자가 기록은 꾸준함
--   D-002  기증 7개월 차, 지금 6개월 검사 기간
--   D-003  기증 2년 차, 2년 검사를 막 놓쳤고 자가 기록도 6주째 끊김

-- ---------- 공여자 ----------
INSERT INTO donors (donor_code, birth_year, sex, donation_date, predonation_egfr) VALUES
    ('D-002', 1972, 'M', '2026-04-10', 118),
    ('D-003', 1988, 'F', '2024-09-01', 102)
ON CONFLICT (donor_code) DO NOTHING;

-- ---------- 추적검사 일정: 일정이 없는 공여자에게 6개월 · 1년 · 2년 · 3년 생성 ----------
INSERT INTO followups (donor_id, visit_name, due_date)
SELECT d.donor_id, v.visit_name, (d.donation_date + v.gap)::date
FROM donors d
CROSS JOIN (VALUES ('기증 후 6개월', interval '6 months'),
                   ('기증 후 1년',   interval '1 year'),
                   ('기증 후 2년',   interval '2 years'),
                   ('기증 후 3년',   interval '3 years')) AS v(visit_name, gap)
WHERE NOT EXISTS (SELECT 1 FROM followups f WHERE f.donor_id = d.donor_id);

-- D-003은 6개월 · 1년 검사를 예정일 일주일 뒤에 받음
UPDATE followups f SET completed_date = f.due_date + 7
FROM donors d
WHERE f.donor_id = d.donor_id AND d.donor_code = 'D-003'
  AND f.visit_name IN ('기증 후 6개월', '기증 후 1년') AND f.completed_date IS NULL;

-- ---------- 병원 검사 결과 (연구 담당자 입력, 입력 시각은 검사 3일 뒤로) ----------
-- 기증 직후 결과 (추적검사와 연결 없음)
INSERT INTO lab_results (donor_id, tested_at, creatinine, egfr, urine_protein, entered_by, created_at)
SELECT d.donor_id, v.tested_at::date, v.cr, v.egfr, 'Negative', 'CRC 데모', v.tested_at::date + 3
FROM donors d
JOIN (VALUES ('D-001', '2025-06-22', 1.18, 74),
             ('D-002', '2026-04-17', 1.31, 66),
             ('D-003', '2024-09-08', 1.12, 58)) AS v(code, tested_at, cr, egfr)
  ON d.donor_code = v.code
ON CONFLICT (donor_id, tested_at) DO NOTHING;

-- 완료된 추적검사의 결과 (완료일 = 검사일, 해당 추적검사와 연결)
INSERT INTO lab_results (donor_id, tested_at, creatinine, egfr, urine_protein, followup_id, entered_by, created_at)
SELECT f.donor_id, f.completed_date, v.cr, v.egfr, v.up, f.followup_id, 'CRC 데모', f.completed_date + 3
FROM followups f
JOIN donors d ON d.donor_id = f.donor_id
JOIN (VALUES ('D-001', '기증 후 6개월', 1.02, 88, 'Negative'),
             ('D-003', '기증 후 6개월', 1.01, 66, 'Negative'),
             ('D-003', '기증 후 1년',   0.98, 68, 'Trace')) AS v(code, visit_name, cr, egfr, up)
  ON d.donor_code = v.code AND f.visit_name = v.visit_name
WHERE f.completed_date IS NOT NULL
ON CONFLICT (donor_id, tested_at) DO NOTHING;

-- ---------- 공여자 자가 기록 (매주 혈압 · 체중) ----------
-- D-001: 최근 24주 동안 매주
INSERT INTO home_measurements (donor_id, measured_at, systolic_bp, diastolic_bp, weight_kg)
SELECT d.donor_id, w::date,
       119 + (random() * 8 - 4)::int, 77 + (random() * 6 - 3)::int, round((60 + random() - 0.5)::numeric, 1)
FROM donors d,
     generate_series(current_date - interval '23 weeks', current_date - interval '1 day', interval '1 week') AS w
WHERE d.donor_code = 'D-001'
ON CONFLICT (donor_id, measured_at) DO NOTHING;

-- D-002: 최근 12주 동안 매주 (혈압이 조금씩 오르는 흐름)
INSERT INTO home_measurements (donor_id, measured_at, systolic_bp, diastolic_bp, weight_kg)
SELECT d.donor_id, w::date,
       124 + (row_number() OVER (ORDER BY w))::int + (random() * 4 - 2)::int,
       80 + (random() * 6 - 3)::int, round((74 + random() - 0.5)::numeric, 1)
FROM donors d,
     generate_series(current_date - interval '12 weeks', current_date - interval '2 days', interval '1 week') AS w
WHERE d.donor_code = 'D-002'
ON CONFLICT (donor_id, measured_at) DO NOTHING;

-- D-003: 20주 전부터 6주 전까지만 기록하고 끊김
INSERT INTO home_measurements (donor_id, measured_at, systolic_bp, diastolic_bp, weight_kg)
SELECT d.donor_id, w::date,
       114 + (random() * 8 - 4)::int, 74 + (random() * 6 - 3)::int, round((55 + random() - 0.5)::numeric, 1)
FROM donors d,
     generate_series(current_date - interval '20 weeks', current_date - interval '6 weeks', interval '1 week') AS w
WHERE d.donor_code = 'D-003'
ON CONFLICT (donor_id, measured_at) DO NOTHING;

-- ---------- 확인 ----------
SELECT d.donor_code,
       (SELECT count(*) FROM home_measurements h WHERE h.donor_id = d.donor_id) AS 자가기록,
       (SELECT count(*) FROM lab_results l WHERE l.donor_id = d.donor_id)       AS 검사결과,
       (SELECT count(*) FROM followups f WHERE f.donor_id = d.donor_id)         AS 추적검사
FROM donors d ORDER BY d.donor_code;

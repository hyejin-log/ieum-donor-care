-- 이음: 데모 데이터 (모두 가상 값). 오늘 날짜를 기준으로 만들어서 언제 실행해도 같은 상황이 된다.
--   D-001  기증 17개월 · 6개월 검사 완료, 1년 검사 결과가 EMR에만 있음(가져오면 완료) · 자가 기록 꾸준함
--   D-002  기증 7개월 · 지금 6개월 검사 기간 · 혈압이 조금씩 오름
--   D-003  기증 26개월 · 2년 검사 놓침 · 자가 기록 6주째 끊김
-- 계정(users)과 공여자 행(donors)은 지우지 않는다. 기록만 지우고 다시 넣는다.

TRUNCATE chat_messages, consult_requests, lab_results, home_measurements, followups RESTART IDENTITY;

-- donated_side: 기증한 쪽 신장 (L 왼쪽 · R 오른쪽). 생체 기증은 왼쪽이 더 흔하다.
INSERT INTO donors (donor_code, birth_year, sex, donation_date, predonation_egfr, donated_side) VALUES
    ('D-001', 1968, 'F', current_date - interval '17 months', 135, 'L'),
    ('D-002', 1972, 'M', current_date - interval '7 months', 118, 'L'),
    ('D-003', 1988, 'F', current_date - interval '26 months', 102, 'R')
ON CONFLICT (donor_code) DO UPDATE SET birth_year = EXCLUDED.birth_year, sex = EXCLUDED.sex,
    donation_date = EXCLUDED.donation_date, predonation_egfr = EXCLUDED.predonation_egfr,
    donated_side = EXCLUDED.donated_side;

-- 추적검사 일정: 6개월 · 1년 · 2년 · 3년
INSERT INTO followups (donor_id, visit_name, due_date)
SELECT d.donor_id, v.visit_name, (d.donation_date + v.gap)::date
FROM donors d
CROSS JOIN (VALUES ('기증 후 6개월', interval '6 months'), ('기증 후 1년', interval '1 year'),
                   ('기증 후 2년', interval '2 years'), ('기증 후 3년', interval '3 years')) AS v(visit_name, gap)
WHERE d.donor_code IN ('D-001', 'D-002', 'D-003');

-- 완료한 추적검사: 예정일 5~7일 뒤에 받음
UPDATE followups f SET completed_date = f.due_date + 5
FROM donors d WHERE f.donor_id = d.donor_id
  AND ((d.donor_code = 'D-001' AND f.visit_name = '기증 후 6개월')
    OR (d.donor_code = 'D-003' AND f.visit_name IN ('기증 후 6개월', '기증 후 1년')));

-- 기증 직후 결과 (추적검사와 연결 없음)
INSERT INTO lab_results (donor_id, tested_at, creatinine, egfr, urine_protein, entered_by, created_at)
SELECT d.donor_id, (d.donation_date + 7)::date, v.cr, v.egfr, 'Negative', 'CRC 데모', d.donation_date + 10
FROM donors d JOIN (VALUES ('D-001', 1.18, 74), ('D-002', 1.31, 66), ('D-003', 1.12, 58)) AS v(code, cr, egfr)
  ON d.donor_code = v.code;

-- 완료한 추적검사의 결과
INSERT INTO lab_results (donor_id, tested_at, creatinine, egfr, urine_protein, followup_id, entered_by, created_at)
SELECT f.donor_id, f.completed_date, v.cr, v.egfr, v.up, f.followup_id, 'CRC 데모', f.completed_date + 3
FROM followups f JOIN donors d ON d.donor_id = f.donor_id
JOIN (VALUES ('D-001', '기증 후 6개월', 1.02, 88, 'Negative'),
             ('D-003', '기증 후 6개월', 1.01, 66, 'Negative'),
             ('D-003', '기증 후 1년',   0.98, 68, 'Trace')) AS v(code, visit_name, cr, egfr, up)
  ON d.donor_code = v.code AND f.visit_name = v.visit_name
WHERE f.completed_date IS NOT NULL;

-- 자가 기록: 사람이 잰 값처럼 날마다 조금씩 들쭉날쭉하게.
--   random() 대신 hashtext(날짜)로 만든 값이라 다시 넣어도 같은 날짜면 같은 값이 나온다.
-- D-001: 정상 범위에서 꾸준히 기록, 가끔 하루씩 빠짐 (어제 기록은 항상 있음)
INSERT INTO home_measurements (donor_id, measured_at, systolic_bp, diastolic_bp, weight_kg)
SELECT d.donor_id, w::date,
       112 + abs(hashtext('s1' || w::date)) % 11 + abs(hashtext('t1' || w::date)) % 4,
       71 + abs(hashtext('d1' || w::date)) % 9,
       59.3 + (abs(hashtext('w1' || w::date)) % 9) / 10.0
FROM donors d, generate_series(current_date - 83, current_date - 1, interval '1 day') AS w
WHERE d.donor_code = 'D-001'
  AND (abs(hashtext('m1' || w::date)) % 8 <> 0 OR w::date = current_date - 1);

-- D-002: 이틀에 한 번 기록, 혈압이 서서히 올라 집 혈압 기준(135/85) 바로 아래까지 감 (기준은 넘지 않게)
INSERT INTO home_measurements (donor_id, measured_at, systolic_bp, diastolic_bp, weight_kg)
SELECT d.donor_id, w::date,
       120 + n / 5 + abs(hashtext('s2' || w::date)) % 7,
       77 + n / 10 + abs(hashtext('d2' || w::date)) % 4,
       73.4 + n / 40.0 + (abs(hashtext('w2' || w::date)) % 7) / 10.0
FROM donors d, generate_series(current_date - 83, current_date - 2, interval '2 days') WITH ORDINALITY AS g(w, n)
WHERE d.donor_code = 'D-002';

-- D-003: 일주일에 한 번 기록하다 6주 전부터 끊김 (자가 기록 끊김 경고 사례)
INSERT INTO home_measurements (donor_id, measured_at, systolic_bp, diastolic_bp, weight_kg)
SELECT d.donor_id, w::date,
       109 + abs(hashtext('s3' || w::date)) % 10,
       69 + abs(hashtext('d3' || w::date)) % 8,
       54.6 + (abs(hashtext('w3' || w::date)) % 8) / 10.0
FROM donors d, generate_series(current_date - 140, current_date - 42, interval '7 days') AS w
WHERE d.donor_code = 'D-003';

"""
합성(가짜) 공여자 데이터 만들기.
실제 환자 데이터가 없으므로, '이런 공여자는 다음 추적검사를 놓치기 쉽다'는 가정으로 데이터를 만든다.
→ 이 데이터로 학습한 모델은 '파이프라인 시연용'이며 임상적으로 검증된 모델이 아니다.

실행: python ml/make_synthetic.py   →  ml/synthetic_donors.csv
"""
from pathlib import Path

import numpy as np
import pandas as pd

rng = np.random.default_rng(42)          # 같은 결과가 나오도록 난수 고정
N = 2000

engagement = rng.normal(0, 1, N)         # 눈에 보이지 않는 '관리 참여도'
years = rng.uniform(0.4, 5, N)
age = np.clip(rng.normal(46, 11, N), 20, 75).round()

record_rate = 1 / (1 + np.exp(-(0.8 + 1.3 * engagement - 0.15 * years + rng.normal(0, 0.6, N))))
weeks_gap = np.clip(rng.exponential(np.exp(0.6 - 0.9 * engagement)), 0, 26).round()
prior_visits = np.clip(np.floor(years * 1.2), 0, 6)                     # 6개월·1년·2년… 지난 검사 수 근사
prior_missed = np.minimum(rng.poisson(np.exp(-1.6 - 0.8 * engagement + 0.25 * years)), prior_visits)
mean_delay = np.where(prior_visits > prior_missed,
                      np.clip(rng.gamma(2, np.exp(2.4 - 0.4 * engagement)), 0, 120), 0).round()
bp_alert = (rng.random(N) < 0.15).astype(int)

# 정답(놓침 여부)을 만드는 '가정한 규칙' + 우연(노이즈)
logit = (-2.3 - 2.4 * (record_rate - 0.6) + 0.10 * weeks_gap + 0.8 * prior_missed
         + 0.012 * mean_delay + 0.30 * (years - 2) - 0.025 * (age - 46) + 0.2 * bp_alert
         + rng.normal(0, 0.7, N))
missed_next = (rng.random(N) < 1 / (1 + np.exp(-logit))).astype(int)

df = pd.DataFrame({
    "record_rate_12w": record_rate.round(3),
    "weeks_since_home": weeks_gap.astype(int),
    "prior_missed": prior_missed.astype(int),
    "mean_delay_days": mean_delay.astype(int),
    "years_since_donation": years.round(2),
    "age": age.astype(int),
    "bp_alert": bp_alert,
    "missed_next": missed_next,          # 정답: 다음 추적검사를 놓쳤으면 1
})
out = Path(__file__).with_name("synthetic_donors.csv")
df.to_csv(out, index=False)
print(f"{out} 저장 · {len(df)}명 · 놓침 비율 {df['missed_next'].mean():.1%}")

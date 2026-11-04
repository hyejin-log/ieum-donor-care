"""
추적검사 이탈 위험 모델의 입력값(특징, feature) 정의.
학습(합성 데이터)과 서버(실제 DB) 양쪽에서 같은 이름과 순서를 쓴다.
"""
FEATURES = [
    "record_rate_12w",      # 최근 12주 자가 기록률 (0~1)
    "weeks_since_home",     # 마지막 자가 기록 후 지난 주 수 (기록 없으면 26)
    "prior_missed",         # 지금까지 놓친 추적검사 수
    "mean_delay_days",      # 지금까지 받은 추적검사가 예정일보다 평균 며칠 늦었나 (없으면 0)
    "years_since_donation", # 기증 후 햇수
    "age",                  # 나이
    "bp_alert",             # 혈압 경고가 있으면 1
]

LABELS_KO = {
    "record_rate_12w": "자가 기록률이 낮음",
    "weeks_since_home": "자가 기록이 끊긴 기간이 김",
    "prior_missed": "이전에 놓친 추적검사가 있음",
    "mean_delay_days": "이전 검사를 늦게 받음",
    "years_since_donation": "기증 후 시간이 오래 지남",
    "age": "나이",
    "bp_alert": "혈압 경고",
}

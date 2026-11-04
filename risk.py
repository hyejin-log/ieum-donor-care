"""
추적검사 이탈 위험 예측 (서버용).
ml/train.py가 만든 ml/dropout_model.json(로지스틱 회귀의 평균·표준편차·계수)을 읽어 계산한다.
scikit-learn 없이 덧셈·곱셈과 시그모이드만으로 예측할 수 있다.
"""
import json
import math
from datetime import date
from pathlib import Path

from ml.features import FEATURES, LABELS_KO

MODEL_PATH = Path(__file__).parent / "ml" / "dropout_model.json"
MODEL = json.loads(MODEL_PATH.read_text(encoding="utf-8")) if MODEL_PATH.exists() else None


def donor_features(visits: list[dict], record_rate: float, weeks_since_home: int | None,
                   donation_date: date, birth_year: int | None, bp_alert: bool, today: date) -> dict | None:
    """
    DB에서 읽은 값으로 모델 입력값을 만든다.
    다음에 받아야 할 검사(검사 기간 또는 예정)가 없으면 예측하지 않는다(None).
    """
    nxt = next((v for v in visits if v["status"] in ("window", "upcoming")), None)
    if nxt is None:
        return None
    before = [v for v in visits if v["due_date"] < nxt["due_date"]]
    delays = [max((v["completed_date"] - v["due_date"]).days, 0) for v in before if v["completed_date"]]
    return {
        "record_rate_12w": record_rate,
        "weeks_since_home": 26 if weeks_since_home is None else min(weeks_since_home, 26),
        "prior_missed": sum(v["status"] == "missed" for v in before),
        "mean_delay_days": sum(delays) / len(delays) if delays else 0,
        "years_since_donation": (today - donation_date).days / 365.25,
        "age": today.year - birth_year if birth_year else 46,
        "bp_alert": int(bp_alert),
        "_visit": nxt["visit_name"],
    }


def predict(features: dict | None) -> dict | None:
    """위험 확률(%)과 단계, 확률을 가장 많이 올린 이유 2개를 돌려준다."""
    if MODEL is None or features is None:
        return None
    # 표준화: (값 - 평균) / 표준편차  →  계수와 곱해 더한다
    z = [(features[f] - m) / s for f, m, s in zip(FEATURES, MODEL["mean"], MODEL["scale"])]
    contrib = {f: c * v for f, c, v in zip(FEATURES, MODEL["coef"], z)}
    logit = MODEL["intercept"] + sum(contrib.values())
    p = 1 / (1 + math.exp(-logit))                     # 시그모이드: 0~1 확률로 바꾼다
    level = "high" if p >= MODEL["levels"]["high"] else "mid" if p >= MODEL["levels"]["low"] else "low"
    reasons = [LABELS_KO[f] for f, c in sorted(contrib.items(), key=lambda kv: -kv[1]) if c > 0.15][:2]
    return {"percent": round(p * 100), "level": level, "visit": features["_visit"], "reasons": reasons}

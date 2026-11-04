"""
추적검사 이탈 위험 모델 학습.
- 로지스틱 회귀(설명하기 쉬움)와 랜덤 포레스트(비교용)를 같은 데이터로 평가한다.
- 서버에는 로지스틱 회귀를 JSON(평균·표준편차·계수)으로 저장해 쓴다. → 서버에는 scikit-learn이 필요 없다.

실행: pip install scikit-learn
      python ml/make_synthetic.py
      python ml/train.py           →  ml/dropout_model.json
"""
import json
from datetime import date
from pathlib import Path

import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import cross_val_score, train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from features import FEATURES

HERE = Path(__file__).parent
df = pd.read_csv(HERE / "synthetic_donors.csv")
X, y = df[FEATURES], df["missed_next"]

# 학습용 75% / 평가용 25%. 놓침 비율이 양쪽에 같게(stratify)
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.25, stratify=y, random_state=42)

models = {
    "logistic": make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)),
    "random_forest": RandomForestClassifier(n_estimators=300, min_samples_leaf=10, random_state=42),
}
metrics = {}
for name, model in models.items():
    cv_auc = cross_val_score(model, X_train, y_train, cv=5, scoring="roc_auc")   # 5겹 교차검증
    model.fit(X_train, y_train)
    p = model.predict_proba(X_test)[:, 1]
    metrics[name] = {"cv_auc_mean": round(cv_auc.mean(), 3), "cv_auc_std": round(cv_auc.std(), 3),
                     "test_auc": round(roc_auc_score(y_test, p), 3), "test_brier": round(brier_score_loss(y_test, p), 3)}
    print(f"{name:14s} CV AUC {cv_auc.mean():.3f} ± {cv_auc.std():.3f} · test AUC {metrics[name]['test_auc']:.3f} · Brier {metrics[name]['test_brier']:.3f}")

# 서버용으로 로지스틱 회귀를 JSON으로 내보내기
scaler, lr = models["logistic"].named_steps["standardscaler"], models["logistic"].named_steps["logisticregression"]
model_json = {
    "name": "추적검사 이탈 위험 (로지스틱 회귀)",
    "trained_on": "합성 데이터 (synthetic_donors.csv) · 임상 검증 전 시연용",
    "trained_at": date.today().isoformat(),
    "features": FEATURES,
    "mean": scaler.mean_.round(6).tolist(),
    "scale": scaler.scale_.round(6).tolist(),
    "coef": lr.coef_[0].round(6).tolist(),
    "intercept": round(float(lr.intercept_[0]), 6),
    "metrics": metrics,
    "base_rate": round(float(y.mean()), 3),
    "levels": {"low": 0.2, "high": 0.4},
}
(HERE / "dropout_model.json").write_text(json.dumps(model_json, ensure_ascii=False, indent=1), encoding="utf-8")
print("계수(표준화 후):", dict(zip(FEATURES, model_json["coef"])))
print("ml/dropout_model.json 저장")

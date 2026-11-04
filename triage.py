"""
공여자 문장 분류 (서버용, numpy만 사용): 응급 / 문의 / 일반
ml/train_triage.py(PyTorch)가 저장한 가중치로 같은 계산을 한다.
  문장 → 글자 조각 번호 → 조각 벡터 평균 → (W1·h + b1) → ReLU → (W2·h + b2) → softmax
"""
import json
from pathlib import Path

import numpy as np

from ml.triage_features import LABELS, ngram_ids

HERE = Path(__file__).parent / "ml"
W = np.load(HERE / "triage_model.npz") if (HERE / "triage_model.npz").exists() else None
META = json.loads((HERE / "triage_model.json").read_text(encoding="utf-8")) if (HERE / "triage_model.json").exists() else {}
THRESHOLD = META.get("emergency_threshold", 0.35)   # 응급은 놓치지 않도록 기준을 낮게 둔다


def classify(text: str) -> dict | None:
    if W is None:
        return None
    h = W["emb"][ngram_ids(text)].mean(axis=0)            # EmbeddingBag(mean)
    h = np.maximum(W["w1"] @ h + W["b1"], 0)              # Linear + ReLU (Dropout은 학습 때만)
    logits = W["w2"] @ h + W["b2"]
    p = np.exp(logits - logits.max())
    p = p / p.sum()                                       # softmax
    probs = {label: round(float(v), 3) for label, v in zip(LABELS, p)}
    label = "응급" if probs["응급"] >= THRESHOLD else LABELS[int(p.argmax())]
    return {"label": label, "probs": probs}


def is_emergency(text: str) -> bool:
    r = classify(text)
    return bool(r and r["label"] == "응급")

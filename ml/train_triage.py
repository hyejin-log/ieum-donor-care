"""
공여자 문장 분류 딥러닝 모델 학습 (PyTorch): 응급 / 문의 / 일반

모델 구조 (fastText 방식의 작은 신경망)
  문장 → 글자 조각 번호들 → [EmbeddingBag] 조각마다 32차원 벡터를 찾아 평균
       → [Linear 32→32 + ReLU] → [Dropout] → [Linear 32→3] → 응급·문의·일반 점수

평가: 같은 틀(template)에서 나온 문장이 학습과 평가에 섞이지 않도록 GroupKFold(5겹)로 나눈다.
      처음 보는 문장 틀에서도 맞히는지를 보는 것이다. 비교용으로 로지스틱 회귀도 같은 방식으로 평가한다.
저장: 서버에서 PyTorch 없이 numpy로 계산할 수 있도록 가중치를 ml/triage_model.npz 로 저장한다.

실행: pip install torch scikit-learn
      python ml/triage_data.py
      python ml/train_triage.py
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import GroupKFold

from triage_features import BUCKETS, LABELS, ngram_ids

HERE = Path(__file__).parent
torch.manual_seed(0)
np.random.seed(0)

df = pd.read_csv(HERE / "triage_sentences.csv")
ids_list = [ngram_ids(t) for t in df["text"]]
y = df["label"].to_numpy()
groups = df["template_id"].to_numpy()


class TriageNet(nn.Module):
    def __init__(self, buckets=BUCKETS, dim=32, hidden=32, n_classes=3):
        super().__init__()
        self.emb = nn.EmbeddingBag(buckets, dim, mode="mean")   # 조각 벡터들의 평균 = 문장 벡터
        self.fc1 = nn.Linear(dim, hidden)
        self.drop = nn.Dropout(0.2)
        self.fc2 = nn.Linear(hidden, n_classes)

    def forward(self, ids, offsets):
        h = self.emb(ids, offsets)
        h = torch.relu(self.fc1(h))
        return self.fc2(self.drop(h))                           # softmax는 손실 함수 안에서 계산


def batchify(idx):
    """여러 문장의 조각 번호를 한 줄로 이어 붙이고, 각 문장이 어디서 시작하는지(offsets)를 함께 넘긴다."""
    flat, offsets = [], []
    for i in idx:
        offsets.append(len(flat))
        flat.extend(ids_list[i])
    return torch.tensor(flat), torch.tensor(offsets)


def train(train_idx, epochs=40, batch=32):
    model = TriageNet()
    counts = np.bincount(y[train_idx], minlength=3)
    weights = torch.tensor(len(train_idx) / (3 * counts), dtype=torch.float32)   # 적은 라벨에 가중치
    loss_fn = nn.CrossEntropyLoss(weight=weights)
    opt = torch.optim.AdamW(model.parameters(), lr=0.01, weight_decay=1e-4)
    for _ in range(epochs):
        model.train()
        order = np.random.permutation(train_idx)
        for s in range(0, len(order), batch):
            b = order[s:s + batch]
            ids, offsets = batchify(b)
            loss = loss_fn(model(ids, offsets), torch.tensor(y[b]))
            opt.zero_grad()
            loss.backward()
            opt.step()
    return model


def predict(model, idx):
    model.eval()
    with torch.no_grad():
        ids, offsets = batchify(idx)
        return torch.softmax(model(ids, offsets), dim=1).numpy()


# ---------- 1) 처음 보는 문장 틀로 평가 (GroupKFold 5겹) ----------
dl_prob = np.zeros((len(df), 3))
lr_pred = np.zeros(len(df), dtype=int)
X_bow = np.zeros((len(df), BUCKETS))                     # 비교용: 조각 개수 표 + 로지스틱 회귀
for i, ids in enumerate(ids_list):
    np.add.at(X_bow[i], ids, 1)
X_bow /= X_bow.sum(1, keepdims=True)

for fold, (tr, te) in enumerate(GroupKFold(n_splits=5).split(df, y, groups)):
    dl_prob[te] = predict(train(tr), te)
    lr = LogisticRegression(max_iter=3000, C=30, class_weight="balanced").fit(X_bow[tr], y[tr])
    lr_pred[te] = lr.predict(X_bow[te])
    print(f"fold {fold + 1}: 평가 문장 {len(te)}개, 처음 보는 틀 {len(set(groups[te]))}개")

dl_pred = dl_prob.argmax(1)
print("\n[딥러닝] 처음 보는 문장 틀에서의 성능")
print(classification_report(y, dl_pred, target_names=LABELS, digits=3))
print("혼동 행렬 (행: 정답, 열: 예측)\n", confusion_matrix(y, dl_pred))
print("\n[비교: 로지스틱 회귀]")
print(classification_report(y, lr_pred, target_names=LABELS, digits=3))

# 응급은 놓치면 안 되므로, 응급 확률 기준값을 낮췄을 때 응급 재현율(놓치지 않은 비율)을 본다
for th in (0.5, 0.35, 0.25):
    flag = dl_prob[:, 2] >= th
    recall = (flag & (y == 2)).sum() / (y == 2).sum()
    false_alarm = (flag & (y != 2)).sum() / (y != 2).sum()
    print(f"응급 기준 {th:.2f}: 응급 재현율 {recall:.3f} · 응급이 아닌데 응급으로 본 비율 {false_alarm:.3f}")

# ---------- 2) 전체 데이터로 최종 학습 후 저장 ----------
final = train(np.arange(len(df)))
np.savez(HERE / "triage_model.npz",
         emb=final.emb.weight.detach().numpy(),
         w1=final.fc1.weight.detach().numpy(), b1=final.fc1.bias.detach().numpy(),
         w2=final.fc2.weight.detach().numpy(), b2=final.fc2.bias.detach().numpy())
report = classification_report(y, dl_pred, target_names=LABELS, output_dict=True)
(HERE / "triage_model.json").write_text(json.dumps({
    "name": "공여자 문장 분류 (EmbeddingBag + MLP, PyTorch)",
    "trained_on": "합성 문장 (triage_sentences.csv) · 임상 검증 전 시연용",
    "labels": LABELS, "buckets": BUCKETS, "emergency_threshold": 0.35,
    "group_cv": {k: {m: round(v, 3) for m, v in report[k].items() if m != "support"} for k in LABELS},
}, ensure_ascii=False, indent=1), encoding="utf-8")
print("\nml/triage_model.npz, ml/triage_model.json 저장")

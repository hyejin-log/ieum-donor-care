"""
RAG(검색 증강 생성)의 '검색' 부분.

  knowledge/*.md ──[1. 자르기]──▶ 조각(## 제목 하나 = 조각 하나)
  질문 ──[2. 글자 조각으로 바꾸기]──▶ BM25 점수 ──[3. 상위 3개 + 기준 점수]──▶ agent.py 가 LLM에 [자료]로 넣음

왜 임베딩(벡터 검색)이 아니라 BM25인가
  - 문서가 11개, 조각이 40개 안팎이라 단어 겹침만으로도 충분히 찾을 수 있다(eval/rag_cases.csv 로 측정).
  - 지금 쓰는 LLM 서비스(Groq)로는 임베딩을 만들지 않고, 임베딩 모델을 서버에 넣으면 배포 용량과 첫 응답이 커진다.
  - 결과가 항상 같고, 왜 이 조각이 뽑혔는지(겹친 글자 조각) 설명할 수 있다.
  조각이 수천 개로 늘거나 같은 뜻을 전혀 다른 말로 묻는 질문이 많아지면, 임베딩 검색을 더해 둘을 섞는다(하이브리드).

한국어는 띄어쓰기와 조사(이/가/은/는)가 사람마다 달라서 단어 대신 '글자 2~3개 조각'을 쓴다.
  예: "진통제 먹어도" → 진통, 통제, 제먹, 먹어, 어도, 진통제, 통제먹, ...
"""
import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

KDIR = Path(__file__).parent / "knowledge"
NGRAMS = (2, 3)
K1, B = 1.2, 0.75          # BM25 기본값: K1 = 같은 조각이 여러 번 나올 때 점수가 늘어나는 정도, B = 긴 조각 불이익
TOP_K = 3
MIN_SCORE = 0.45           # 질문 글자 조각 하나당 평균 점수. 이보다 낮으면 '자료에 없음'(eval/rag_cases.csv 로 정한 값)
HEAD_WEIGHT = 2            # 제목에 나온 말은 본문보다 두 배로 센다

# 말끝(요/나요/합니다 …)은 어느 조각과도 겹쳐 점수를 흐리므로 낱말 끝에서 떼고 센다 (문서와 질문 모두)
ENDINGS = sorted(["한가요", "인가요", "할까요", "을까요", "될까요", "되나요", "하나요", "나요", "가요", "까요", "어요", "아요", "해요",
                  "돼요", "되요", "세요", "예요", "이에요", "에요", "해야", "하면", "해도", "어때", "대요", "데요", "줘", "죠",
                  "습니다", "합니다", "입니다", "니다", "어야", "아야", "할때", "일때", "때"], key=len, reverse=True)
MIN_COVER = 0.18           # 질문의 드문 말을 이 비율 이상 덮어야 함
STRICT = {"min_score": 1.0, "min_cover": 0.3}   # 질문에 안내 주제어가 없을 때는 더 확실할 때만 자료로 답한다
SURE_COVER = 0.3           # 겹친 조각이 하나뿐이면 이만큼은 덮어야 함 ('오늘' 하나만 겹치는 것을 막음)


def normalize(text: str) -> str:
    return re.sub(r"[^0-9a-z가-힣 ]", " ", text.lower())


def _stem(word: str) -> str:
    for e in ENDINGS:
        if len(word) > len(e) + 1 and word.endswith(e):
            return word[: -len(e)]
    return word


PARTICLES = ("은", "는", "을", "를", "이", "가", "도", "에", "의", "로", "와", "과")


def grams(text: str) -> list[str]:
    """낱말마다 말끝을 떼고 글자 2~3개 조각으로. 낱말 경계를 넘는 조각은 만들지 않는다.
    '술은', '물을'처럼 조사를 떼면 한 글자만 남는 낱말은 그 한 글자도 조각으로 쓴다."""
    out = []
    for w in normalize(text).split():
        if w in ENDINGS or w in ("되나요", "괜찮을까요", "어떻게", "얼마나", "뭘", "왜"):
            continue                              # 낱말 전체가 묻는 말이면 버린다
        w = _stem(w)
        if len(w) == 2 and w.endswith(PARTICLES):
            out.append(w[0])
        out += [w[i:i + n] for n in NGRAMS for i in range(len(w) - n + 1)]
    return out


def _parse(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8")
    meta, body = {}, raw
    if raw.startswith("---"):
        _, head, body = raw.split("---", 2)
        for line in head.strip().splitlines():
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    chunks = []
    for i, part in enumerate(re.split(r"\n## ", "\n" + body.strip())):
        part = part.strip()
        if not part:
            continue
        section, _, text = part.partition("\n")
        section = section.lstrip("# ").strip()
        chunks.append({"id": f"{path.stem}#{i}", "doc": path.stem, "title": meta.get("title", path.stem),
                       "section": section, "text": " ".join(text.split()),
                       "source": meta.get("source", ""), "reviewed": meta.get("reviewed", ""),
                       "keywords": meta.get("keywords", "")})
    return chunks


@lru_cache(maxsize=1)
def index() -> dict:
    """처음 한 번만 만든다(서버가 켜질 때). 조각마다 글자 조각 개수표, 전체에서 몇 조각에 나오는지(df)."""
    chunks = [c for p in sorted(KDIR.glob("*.md")) if p.name != "README.md" for c in _parse(p)]
    tfs = []
    for c in chunks:
        # 제목 · 소제목 · 검색어(문서 맨 위 keywords: 사람들이 실제로 쓰는 말)는 본문보다 무겁게 센다
        head = (c["title"] + " " + c["section"] + " " + c["keywords"] + " ") * HEAD_WEIGHT
        tfs.append(Counter(grams(head + c["text"])))
    df = Counter(g for tf in tfs for g in tf)
    n = len(chunks)
    idf = {g: math.log(1 + (n - d + 0.5) / (d + 0.5)) for g, d in df.items()}
    lens = [sum(tf.values()) for tf in tfs]
    return {"chunks": chunks, "tfs": tfs, "idf": idf, "lens": lens, "avg": sum(lens) / max(1, n)}


def search(question: str, k: int = TOP_K, min_score: float = MIN_SCORE, min_cover: float = MIN_COVER) -> list[dict]:
    """질문과 가장 관련 있는 조각 k개. 기준 점수보다 낮은 조각은 버린다(없으면 빈 목록 = 자료에 없음)."""
    ix = index()
    asked = set(grams(question))
    q = [g for g in asked if g in ix["idf"]]
    if not q:
        return []
    # 자료에 아예 없는 낱말(보험, 주식 …)은 가장 드문 낱말만큼 무겁게 센다 → 질문의 핵심이 자료에 없으면 덮는 비율이 낮아짐
    top_idf = max(ix["idf"].values())
    asked_w = sum(ix["idf"].get(g, top_idf) for g in asked)
    scored = []
    for c, tf, ln in zip(ix["chunks"], ix["tfs"], ix["lens"]):
        s, cover_w, hit = 0.0, 0.0, 0
        for g in q:
            f = tf.get(g, 0)
            if f:
                hit += 1
                cover_w += ix["idf"][g]
                s += ix["idf"][g] * f * (K1 + 1) / (f + K1 * (1 - B + B * ln / ix["avg"]))
        # cover = 질문의 '드문 말'을 이 조각이 얼마나 덮는지 (흔한 말은 가볍게)
        scored.append((s / len(asked), cover_w / asked_w, hit, c))
    scored.sort(key=lambda x: -x[0])
    return [{**c, "score": round(s, 3), "cover": round(cv, 2)} for s, cv, hit, c in scored[:k]
            if s >= min_score and cv >= min_cover and (hit >= 2 or cv >= SURE_COVER)]


def context_block(hits: list[dict]) -> str:
    """LLM에 넣을 [자료]. 번호를 붙여 답에서 [1]처럼 가리키게 한다."""
    return "\n".join(f"[{i}] {h['title']} · {h['section']}: {h['text']}" for i, h in enumerate(hits, 1))


def sources(hits: list[dict]) -> list[dict]:
    """화면에 보여 줄 출처 목록(같은 문서는 한 번만)."""
    seen, out = set(), []
    for i, h in enumerate(hits, 1):
        key = (h["doc"], h["section"])
        if key in seen:
            continue
        seen.add(key)
        out.append({"n": i, "title": h["title"], "section": h["section"], "source": h["source"], "reviewed": h["reviewed"]})
    return out

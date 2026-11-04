"""
문장 → 숫자 바꾸기 (학습과 서버에서 똑같이 쓴다)
한국어는 띄어쓰기와 말끝이 사람마다 달라서, 단어 대신 '글자 조각(n-gram)'을 쓴다.
예: "숨이차요" → 글자 1~3개 조각: 숨, 이, 차, 요, 숨이, 이차, 차요, 숨이차, 이차요 …
조각 하나하나를 해시(crc32)로 0 ~ BUCKETS-1 사이 번호로 바꾼다.
(파이썬 hash()는 실행할 때마다 값이 바뀌므로 쓰면 안 된다)
"""
import re
import zlib

BUCKETS = 4096          # 번호 칸 수 (서로 다른 조각이 같은 칸에 들어갈 수 있지만, 작은 모델에는 충분)
NGRAMS = (1, 2, 3)
LABELS = ["일반", "문의", "응급"]


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^0-9a-z가-힣/.]", "", text)     # 띄어쓰기·기호 제거 (숫자, 영문, 한글, / . 만 남김)
    return f"<{text}>"                               # 문장의 시작과 끝 표시


def ngram_ids(text: str) -> list[int]:
    t = normalize(text)
    ids = []
    for n in NGRAMS:
        for i in range(len(t) - n + 1):
            ids.append(zlib.crc32(t[i:i + n].encode("utf-8")) % BUCKETS)
    return ids or [0]

# 이음 · 생체 신장 공여자 장기 추적관리

신장을 기증한 **생체 공여자**가 기증 후 정기 추적검사를 놓치지 않도록 돕는 웹 애플리케이션입니다.
공여자는 집에서 혈압·체중을 기록하고 안내 Agent에게 질문하며, 의료진(연구 코디네이터)은 검사 결과를 입력하고 **검사를 놓쳤거나 위험 신호가 있는 공여자**를 한눈에 확인합니다.

> 임상연구 코디네이터(CRC)로 일하며 대상자 일정·연구 데이터를 관리했던 경험에서 출발했습니다.
> 생체 공여자는 기증 후 시간이 지날수록 추적검사에서 이탈하기 쉬운데, 그 이탈을 **기록 · 경고 · 예측 · 안내**로 줄이는 것이 목표입니다.

> ⚠️ 이 프로젝트의 데이터는 모두 **데모·합성 데이터**이며, 경고 기준값·안내문·모델은 **임상 검증 전 시연용**입니다.

## 화면

<table>
  <tr>
    <td align="center" width="28%"><b>공여자 앱</b></td>
    <td align="center" width="72%"><b>안내 Agent</b> · 내 기록 확인 → 응급 안전 안내 → 일상 대화</td>
  </tr>
  <tr>
    <td valign="top"><img src="docs/images/donor.png" alt="공여자 화면: 기증 후 일수, 이번 주 기록, 다음 추적검사, 혈압 그래프, 최근 검사 결과"></td>
    <td valign="top"><img src="docs/images/agent-chat.png" alt="안내 Agent 대화: 다음 검사 일정 답변, 가슴 통증에 119 안내, 산책 이야기에 기록 권유"></td>
  </tr>
</table>

**의료진 화면** · 놓친 추적검사, 혈압 경고, 이탈 위험 예측, 최근 12주 자가 기록률을 한눈에 보고 연구 데이터를 엑셀로 내려받습니다.

<img src="docs/images/staff.png" alt="의료진 화면: 공여자별 상태, 다음 추적검사와 이탈 위험, 최근 eGFR, 12주 자가 기록 막대">

---

## 주요 기능

| 대상 | 기능 | 사용 기술 |
|---|---|---|
| 공여자 | 주 1회 혈압·체중 기록, 최근 12주 그래프, 다음 추적검사 D-day, 알림함·브라우저 알림 | FastAPI · PostgreSQL · Chart.js |
| 공여자 | **안내 Agent**: 내 기록·일정 질문, 기증 후 생활 안내, 응급 증상 시 119 안내 | LLM(Groq / Ollama) · 딥러닝 문장 분류 |
| 의료진 | 검사 결과 입력 → 해당 추적검사 **자동 완료 처리** | SQL 트랜잭션 · UPSERT |
| 의료진 | 공여자 목록: 놓친 검사 · 검사 기간 · 경고 · 12주 기록률 · **이탈 위험 예측** | 규칙 엔진 · 머신러닝 |
| 의료진 | **연구 데이터 엑셀 내려받기** (시트 5개) | pandas · openpyxl |

## 구조

```mermaid
flowchart LR
    subgraph BR["브라우저"]
        D[공여자 화면<br/>donor.html]
        S[의료진 화면<br/>staff.html]
    end
    subgraph SV["FastAPI 서버 (main.py)"]
        API[REST API<br/>Pydantic 검사]
        R[경고 규칙]
        P[pandas<br/>기록률 · 엑셀]
        M[ML 이탈 위험<br/>risk.py]
        A[안내 Agent<br/>agent.py]
        T[DL 문장 분류<br/>triage.py]
    end
    DB[(PostgreSQL<br/>테이블 4개)]
    LLM[[LLM<br/>Groq 또는 Ollama]]
    D -- HTTP/JSON --> API
    S -- HTTP/JSON --> API
    API -- SQL --> DB
    API --- R & P & M & A
    A --> T
    A -. 자유 질문만 .-> LLM
```

## 데이터베이스

```mermaid
erDiagram
    donors ||--o{ followups : "추적검사 일정"
    donors ||--o{ home_measurements : "자가 기록"
    donors ||--o{ lab_results : "병원 검사"
    followups |o--o{ lab_results : "어느 검사의 결과인지"
    donors {
        int donor_id PK
        text donor_code UK
        date donation_date
        int predonation_egfr
    }
    followups {
        int followup_id PK
        int donor_id FK
        text visit_name
        date due_date
        date completed_date
    }
    home_measurements {
        int home_id PK
        int donor_id FK
        date measured_at
        int systolic_bp
        numeric weight_kg
    }
    lab_results {
        int lab_id PK
        int donor_id FK
        date tested_at
        int egfr
        text urine_protein
        int followup_id FK
        text entered_by
    }
```

- 공여자가 입력하는 값(혈압·체중)과 병원 검사값(eGFR·크레아티닌·단백뇨)은 **입력 주체와 주기가 달라** 테이블을 분리했습니다.
- `UNIQUE(공여자, 날짜)` + `ON CONFLICT DO UPDATE`로 하루 1건을 데이터베이스 수준에서 보장합니다.
- 추적검사 상태(완료 · 검사 기간 · 놓침 · 예정)는 **저장하지 않고 날짜로 계산**합니다. 예정일 ±60일을 검사 기간으로 봅니다.

## 안내 Agent 설계

질문은 위에서부터 차례로 검사해, **AI가 꼭 필요한 질문만** LLM으로 보냅니다.

| 순서 | 판단 | 답하는 방식 | 응답 시간 |
|---|---|---|---|
| ① | 응급 (단어 목록 **또는** DL 분류) | 119·응급실 안내 | 즉시 |
| ② | 일반 대화 (DL 분류) | 정해진 답 + 이번 주 기록 권유 | 즉시 |
| ③ | 안내문 주제 (진통제·운동·eGFR…) | 이식센터 안내문 그대로 | 즉시 |
| ④ | 내 기록 (검사 일정·혈압·검사 결과) | 실제 값으로 답 | 즉시 |
| ⑤ | 그 밖의 자유 질문 | LLM (스트리밍) | Groq 약 2~4초 |

**안전 설계**
- LLM에는 서버가 조회한 **이 공여자의 자료만** 넣어 줍니다. 공여자 코드는 서버가 고정하므로 다른 공여자의 기록은 볼 수 없습니다.
- 답을 내보내기 전에 **"정상 범위" 같은 판단 표현을 검사**해 걸리면 의료진 확인 안내로 바꿉니다.
- 진단·처방·수치 판단은 하지 않도록 지시하고, 검사 결과 해석은 의료진이 전달하도록 했습니다.

**실제로 겪은 문제와 해결**
- 로컬 3B 모델이 도구를 부르지 않고 혈압을 "120/80, 정상 범위"라고 **지어냄** → 도구 호출 대신 서버가 자료를 넣어 주는 방식 + 출력 검사로 해결
- 로컬 모델 응답이 느림 → 측정해 보니 첫 글자까지 59초 중 대부분이 **프롬프트 읽기 시간** → 프롬프트 축소 · 공여자 자료 미리 읽혀 두기 · 스트리밍으로 **첫 응답 59초 → 3.5초**
- 클라우드(Groq)를 선택지로 추가: 자유 질문 **18~22초 → 2~4초**. 실제 의료 환경에서는 `.env` 한 줄로 **기록이 밖으로 나가지 않는 로컬 모델**로 바꿀 수 있게 했습니다.

## 머신러닝 · 딥러닝

### 추적검사 이탈 위험 (ML)
- 입력값 7개: 12주 기록률, 기록이 끊긴 주 수, 이전에 놓친 검사 수, 평균 지연일, 기증 후 햇수, 나이, 혈압 경고
- 로지스틱 회귀 vs 랜덤 포레스트, 5겹 교차검증 AUC **0.812 vs 0.801** → 설명하기 쉬운 로지스틱 회귀 선택
- 서버는 계수를 JSON으로 읽어 계산하고, 확률을 가장 많이 올린 **이유 2개**를 함께 보여 줍니다.

### 공여자 문장 분류: 응급 / 문의 / 일반 (DL, PyTorch)
- 한국어 띄어쓰기·말끝 차이에 강하도록 **글자 1~3개 조각**을 사용 (EmbeddingBag → Linear+ReLU → Linear)
- 같은 문장 틀이 학습·평가에 섞이지 않게 **GroupKFold**로 평가: 정확도 0.798 (로지스틱 회귀 0.831), 응급 재현율 0.72~0.77
- 성능이 단순 모델과 비슷해, 안전장치는 **단어 목록과 모델을 함께** 쓰는 구조로 설계했습니다.

> 두 모델 모두 **합성 데이터**로 학습했습니다. 수치는 파이프라인 동작 확인용이며, 실제 적용에는 실제 데이터·IRB 승인·가명처리가 필요합니다.

## 기술 스택

| 영역 | 사용 |
|---|---|
| 백엔드 | Python 3.14, FastAPI, Pydantic v2, psycopg 3 |
| 데이터베이스 | PostgreSQL |
| 데이터 | pandas, openpyxl |
| ML · DL | scikit-learn, PyTorch (서버에서는 numpy로 추론) |
| LLM | Groq (gpt-oss-120b), Ollama (qwen2.5:3b) — 선택 |
| 프론트엔드 | HTML · CSS · JavaScript, Pico CSS, Chart.js |

## 실행 방법

```bash
# 1. 가상환경과 패키지
python -m venv .venv
.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 2. 데이터베이스 (PostgreSQL에 donor_care 데이터베이스를 만든 뒤)
psql -d donor_care -f schema.sql
psql -d donor_care -f seed_demo.sql   # 데모 공여자 D-001 ~ D-003

# 3. 설정: .env.example 을 .env 로 복사하고 DB 비밀번호 등을 채운다
#    LLM 없이도 동작합니다(규칙 답변). 자유 질문 AI를 쓰려면 GROQ_API_KEY 또는 Ollama 설정

# 4. 서버 실행
uvicorn main:app --reload
```

| 주소 | 화면 |
|---|---|
| http://127.0.0.1:8000/ | 공여자 화면 (`?code=D-002`로 데모 공여자 선택) |
| http://127.0.0.1:8000/staff | 의료진 목록 |
| http://127.0.0.1:8000/dashboard?code=D-001 | 공여자 상세 |
| http://127.0.0.1:8000/docs | API 문서 (자동 생성) |
| http://127.0.0.1:8000/health | 서버 · LLM 연결 상태 |

**모델 다시 학습 (선택)**
```bash
pip install scikit-learn torch
python ml/make_synthetic.py && python ml/train.py          # 이탈 위험
python ml/triage_data.py && python ml/train_triage.py      # 문장 분류
```

## 폴더 구조

```
main.py            FastAPI 서버: API, 경고 규칙, pandas 집계, 엑셀 내보내기
agent.py           안내 Agent: 질문 분류, 자료 주입, LLM 호출(스트리밍), 출력 검사
risk.py            이탈 위험 예측 (ML 계수로 계산)
triage.py          문장 분류 (DL 가중치로 numpy 추론)
ml/                합성 데이터 생성 · 학습 스크립트 · 학습된 모델
static/            donor.html · staff.html · dashboard.html
schema.sql         테이블 설계
seed_demo.sql      데모 데이터
docs/              기술 스택 · 데이터베이스 학습서 (PDF)
```

## API

| 메서드 | 주소 | 설명 |
|---|---|---|
| GET | `/donors/{code}/summary` | 기증 후 일수, 추적검사 일정·상태, 최근 eGFR |
| POST | `/home-measurements` | 혈압·체중 저장 (같은 날은 덮어쓰기) |
| POST | `/labs` | 검사 결과 저장 + 추적검사 자동 완료 |
| GET | `/donors/{code}/notifications` | 공여자 알림 계산 |
| GET | `/donors/{code}/alerts` | 의료진용 경고 (혈압 · eGFR · 단백뇨 · 체중) |
| POST | `/donors/{code}/chat/stream` | 안내 Agent (NDJSON 스트리밍) |
| GET | `/staff/overview` | 공여자별 상태 · 경고 · 기록률 · 이탈 위험 |
| GET | `/export/research.xlsx` | 연구 데이터 엑셀 |

## 한계와 다음 단계

- **로그인·권한 없음**: 실제 운영에는 역할별 로그인과 접근 기록(감사 로그)이 필요합니다.
- **경고 기준값·안내문**: 이식센터 의료진의 검토와 승인이 필요합니다.
- **모델**: 실제 추적 데이터로 다시 학습하고 외부 검증이 필요합니다.
- **개인정보**: 외부 LLM 사용 시 정보보호 검토가 필요하며, 기본값은 로컬 모델입니다.
- **배포·푸시 알림**: 현재 브라우저 알림은 화면이 열려 있을 때만 동작합니다(서비스 워커 · Web Push 예정).

---

만든 사람 · [@hyejin-log](https://github.com/hyejin-log)

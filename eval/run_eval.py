"""
안내 Agent 길 고르기(route) 평가.

질문마다 '어느 길로 가야 맞는지' 정답을 붙여 두고(eval/cases.csv), agent.route() 를 그대로 돌려 비교한다.
  safety : 응급 → AI 답 없이 119 안내          guide  : 검증된 안내문 그대로
  record : 내 기록을 조회해 정해진 문장으로      chat   : 인사·근황                            mind   : 마음 건강 → 심리 상담 연결
  llm    : 위 어디에도 해당하지 않아 LLM이 답함
LLM은 부르지 않는다(가짜 기록 사용) → 키·인터넷 없이 몇 초 만에 같은 결과가 나온다.

가장 중요한 숫자는 '응급 재현율'(응급 질문을 응급으로 잡은 비율)이다. 100%가 아니면 실패로 본다.

실행: python eval/run_eval.py          결과: eval/report.md
"""
import csv
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import agent  # noqa: E402

# 가짜 공여자 기록 (D-001 데모와 비슷한 상태): 1년 검사 놓침, 최근 혈압 정상, 오늘 기록 없음
IMPL = {
    "get_donation_summary": lambda: {
        "visits": [{"visit_name": "기증 후 6개월", "status": "done", "due_date": "2025-12-05"},
                   {"visit_name": "기증 후 1년", "status": "missed", "due_date": "2026-06-05"},
                   {"visit_name": "기증 후 2년", "status": "upcoming", "due_date": "2027-06-05"}],
        "egfr_percent_of_predonation": 65},
    "get_home_records": lambda: [{"measured_at": "2026-11-04", "systolic_bp": 121, "diastolic_bp": 72, "weight_kg": 59.9}],
    "get_lab_results": lambda: [{"tested_at": "2025-12-10", "egfr": 88, "creatinine": 1.02, "urine_protein": "Negative"}],
    "get_notifications": lambda: [{"title": "오늘 기록이 아직 없어요"}],
}


def run(cases_path: Path = ROOT / "eval" / "cases.csv") -> dict:
    rows = list(csv.DictReader(cases_path.open(encoding="utf-8")))
    results = []
    for r in rows:
        ans = agent.route(r["question"], IMPL)
        got = ans["mode"] if ans else "llm"
        results.append({**r, "got": got, "ok": got == r["expected"],
                        "detail": (ans or {}).get("topic", "")})
    total = len(results)
    ok = sum(x["ok"] for x in results)
    emer = [x for x in results if x["expected"] == "safety"]
    caught = sum(x["got"] == "safety" for x in emer)
    false_alarm = sum(x["got"] == "safety" for x in results if x["expected"] != "safety")
    by_label = Counter(x["expected"] for x in results)
    by_label_ok = Counter(x["expected"] for x in results if x["ok"])
    return {"results": results, "total": total, "ok": ok, "emer_total": len(emer), "emer_caught": caught,
            "false_alarm": false_alarm, "by_label": by_label, "by_label_ok": by_label_ok}


def report(s: dict) -> str:
    lines = ["# 안내 Agent 길 고르기 평가", "",
             f"- 전체 정확도: **{s['ok']}/{s['total']} ({s['ok'] / s['total']:.0%})**",
             f"- 응급 재현율: **{s['emer_caught']}/{s['emer_total']}** (응급을 응급으로 잡은 비율, 100%여야 함)",
             f"- 응급 오경보: {s['false_alarm']}건 (응급이 아닌데 119 안내)", "",
             "| 길 | 맞음 |", "|---|---|"]
    for k in ["safety", "guide", "record", "chat", "mind", "llm"]:
        lines.append(f"| {k} | {s['by_label_ok'][k]}/{s['by_label'][k]} |")
    fails = [x for x in s["results"] if not x["ok"]]
    lines += ["", f"## 틀린 질문 ({len(fails)}개)", ""]
    if fails:
        lines += ["| 질문 | 정답 | 실제 | 이유 |", "|---|---|---|---|"]
        lines += [f"| {x['question']} | {x['expected']} | {x['got']}{' (' + x['detail'] + ')' if x['detail'] else ''} | {x['why']} |"
                  for x in fails]
    else:
        lines.append("없음")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    # 개발용(cases.csv): 실패를 보고 고친 질문들 → 고친 뒤 100%가 나와도 '외운' 것일 수 있다
    # 확인용(holdout.csv): 고칠 때 보지 않은 새 질문들 → 실제 실력에 가까운 숫자
    dev, hold = run(ROOT / "eval" / "cases.csv"), run(ROOT / "eval" / "holdout.csv")
    text = ("## 개발용 세트 (cases.csv)\n\n" + report(dev).split("\n", 2)[2] +
            "\n## 확인용 세트 (holdout.csv · 고칠 때 보지 않은 질문)\n\n" + report(hold).split("\n", 2)[2])
    text = "# 안내 Agent 길 고르기 평가\n\n" + text
    (ROOT / "eval" / "report.md").write_text(text, encoding="utf-8")
    print(text)
    ok = dev["emer_caught"] == dev["emer_total"] and hold["emer_caught"] == hold["emer_total"]
    sys.exit(0 if ok else 1)   # 응급을 하나라도 놓치면 실패로 끝낸다

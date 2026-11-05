"""
상담 전화 예약 시간표. DB를 보지 않는 순수 함수만 둔다 (테스트하기 쉽게).

- 상담 시간: 평일 09:00 ~ 17:00, 점심(12:00 ~ 13:00) 제외, 30분 단위
- 오늘부터 근무일 5일까지 보여 준다. 지금부터 30분 안의 시간은 고를 수 없다(담당자가 준비할 시간).
- 한국은 서머타임이 없어 +09:00 고정으로 계산한다 (서버리스 환경에 시간대 자료가 없어도 동작).
- 공휴일은 반영하지 않는다: 실제 병원에 쓰면 병원 휴무일 표를 받아 빼야 한다.
"""
from datetime import date, datetime, time, timedelta, timezone

KST = timezone(timedelta(hours=9))
OPEN_HOUR, CLOSE_HOUR = 9, 17
LUNCH = (time(12, 0), time(13, 0))
SLOT_MIN = 30
LEAD_MIN = 30
BUSINESS_DAYS = 5
WEEKDAY_KO = "월화수목금토일"


def day_times(d: date) -> list[datetime]:
    """그날의 예약 가능한 시작 시각들 (KST)"""
    out = []
    t = datetime.combine(d, time(OPEN_HOUR), KST)
    end = datetime.combine(d, time(CLOSE_HOUR), KST)
    while t < end:
        if not (LUNCH[0] <= t.time() < LUNCH[1]):
            out.append(t)
        t += timedelta(minutes=SLOT_MIN)
    return out


def is_open(now: datetime) -> bool:
    """지금 상담 전화를 받을 수 있는 시간인가 (평일 근무 시간, 점심 제외)"""
    k = now.astimezone(KST)
    return k.weekday() < 5 and time(OPEN_HOUR) <= k.time() < time(CLOSE_HOUR) and not (LUNCH[0] <= k.time() < LUNCH[1])


def slot_table(now: datetime, taken: set[datetime]) -> list[dict]:
    """[{date, label, times: [{at, time, taken}]}] — 근무일 5일치. taken 은 이미 예약된 시각들."""
    k = now.astimezone(KST)
    earliest = k + timedelta(minutes=LEAD_MIN)
    taken_utc = {t.astimezone(timezone.utc) for t in taken}
    days, d = [], k.date()
    while len(days) < BUSINESS_DAYS:
        if d.weekday() < 5:
            times = [{"at": t.isoformat(), "time": t.strftime("%H:%M"),
                      "taken": t.astimezone(timezone.utc) in taken_utc}
                     for t in day_times(d) if t >= earliest]
            if times:
                days.append({"date": d.isoformat(),
                             "label": f"{d.month}월 {d.day}일 ({WEEKDAY_KO[d.weekday()]})",
                             "times": times})
            elif d != k.date():
                days.append({"date": d.isoformat(), "label": f"{d.month}월 {d.day}일 ({WEEKDAY_KO[d.weekday()]})", "times": []})
        d += timedelta(days=1)
    return days


def valid_slot(at: datetime, now: datetime) -> bool:
    """고른 시각이 시간표에 있는 시각인가 (시간대 없는 값은 KST로 본다)"""
    if at.tzinfo is None:
        at = at.replace(tzinfo=KST)
    allowed = {datetime.fromisoformat(t["at"]) for day in slot_table(now, set()) for t in day["times"]}
    return at in allowed


def label(at: datetime) -> str:
    k = at.astimezone(KST)
    return f"{k.month}월 {k.day}일 ({WEEKDAY_KO[k.weekday()]}) {k.strftime('%H:%M')}"

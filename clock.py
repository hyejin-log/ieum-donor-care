"""
한국 시간 기준 '오늘'.

배포 서버(Vercel)와 DB(Neon)는 세계 표준시(UTC)로 돌아간다. 그대로 쓰면 한국 시간 00:00~09:00 사이에는
'오늘'이 하루 전으로 계산되어, 같은 화면에서 D-day 가 하루씩 어긋난다 (예: D-303 과 D-304).
그래서 날짜 계산은 모두 이 함수로 한국 날짜를 쓴다. 한국은 서머타임이 없어 +09:00 고정이다.
"""
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def today() -> date:
    return datetime.now(KST).date()

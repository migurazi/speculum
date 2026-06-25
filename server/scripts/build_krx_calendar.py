"""KRX 영업일 캘린더 확장 build 스크립트 — pykrx 거래일 도출 (keyless·authoritative).

ROADMAP_v2 V1a / B2. 기존 `shared/data/calendar/krx-calendar-v1.json` 의 coverage 를
**실제 KRX 거래일에서 도출**해 확장한다. 휴장일을 손으로 지어내지 않고(Fidelity),
유동성 최상위 종목(005930)의 pykrx OHLCV 가 반환하는 거래일 = KRX 공식 거래일에서
"평일 중 비거래일 = 휴장일" 을 역산한다. pykrx 는 keyless 이므로 API 키 불필요.

**왜 005930 인가**: 삼성전자는 매 거래일 거래되므로 OHLCV 데이터 갭 = 실제 시장
휴장(데이터 결손 아님). 유동성 낮은 종목은 거래 없는 날이 생겨 false 휴장 위험.

**provenance**: 2024 분(기존)은 KRX 공식 캘린더 수동 검증(원 reason 보존). 2025 이후
신규분은 pykrx 도출(reason = 고정 공휴일은 명칭, 그 외 generic — 날짜만 authoritative,
사유 라벨은 보조 메타). verified_source 에 두 출처를 명기한다.

**한계(§8 영구부채 완화)**: coverage.max_date = pykrx 마지막 거래일(=오늘 근방). 시간이
지나면 today 가 다시 범위 밖이 되므로 **주기적 재실행**(스케줄러 통합 가능)이 필요하나,
손작업이 아니라 본 스크립트 재실행으로 확장된다. 미래 날짜는 as_of 미래 거부로 차단.

실행:
    python -m scripts.build_krx_calendar              # 2025-01-01 ~ 오늘 확장
    python -m scripts.build_krx_calendar --to 2026-06-25
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from uuid import uuid4

from app.adapters.pykrx_adapter import PykrxAdapter
from app.services.krx_calendar import (
    _DATA_PATH,
    _fill_content_hash,
    kst_today,
)

# 유동성 최상위 — 매 거래일 거래 보장(데이터 갭 = 실 휴장).
_DERIVE_CODE: str = "005930"

# 고정일 공휴일 (MM-DD) → 명칭. 음력(설날/추석/부처님오신날)·대체공휴일·임시공휴일은
# 날짜가 매년 달라 매핑 불가 → generic. 날짜는 pykrx 도출이라 authoritative, 명칭은 보조.
_FIXED_HOLIDAYS: dict[str, str] = {
    "01-01": "신정",
    "03-01": "삼일절",
    "05-01": "근로자의날 (KRX 휴장)",
    "05-05": "어린이날",
    "06-06": "현충일",
    "08-15": "광복절",
    "10-03": "개천절",
    "10-09": "한글날",
    "12-25": "성탄절",
    "12-31": "연말 폐장",
}

_GENERIC_REASON: str = "KRX 휴장 (pykrx 거래일 도출)"


def _reason_for(d: date) -> str:
    """고정 공휴일이면 명칭, 아니면 generic(음력·대체·임시 휴일)."""
    return _FIXED_HOLIDAYS.get(d.strftime("%m-%d"), _GENERIC_REASON)


def _derive_closed_days(extend_from: date, extend_to: date) -> list[dict[str, str]]:
    """[extend_from, extend_to] 의 평일 중 pykrx 비거래일 = 휴장일 리스트."""
    result = PykrxAdapter().fetch_ohlcv_by_date_range(
        _DERIVE_CODE, fromdate=extend_from, todate=extend_to, batch_id=uuid4(),
    )
    trading = {row.trade_date for row in result.data}
    if not trading:
        raise RuntimeError(
            f"pykrx 가 {extend_from}~{extend_to} 거래일을 0건 반환 — 네트워크/범위 확인."
        )
    closed: list[dict[str, str]] = []
    cursor = extend_from
    while cursor <= extend_to:
        # 평일(월~금)인데 거래일이 아니면 휴장. 토/일은 캘린더가 자동 처리(미포함).
        if cursor.weekday() < 5 and cursor not in trading:
            closed.append({"date": cursor.isoformat(), "reason": _reason_for(cursor)})
        cursor += timedelta(days=1)
    return closed


def run(*, extend_to: date) -> None:
    """캘린더 JSON 을 [2025-01-01, extend_to] pykrx 도출 휴장일로 확장 + hash 갱신."""
    body = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    existing_max = date.fromisoformat(body["coverage"]["max_date"])
    extend_from = existing_max + timedelta(days=1)
    if extend_from > extend_to:
        print(f"이미 {existing_max} 까지 coverage — 확장 불필요({extend_to}).")
        return

    new_closed = _derive_closed_days(extend_from, extend_to)
    # 기존 closed_days(2024 수동 검증분, 원 reason 보존) + 신규 도출분.
    body["closed_days"] = body["closed_days"] + new_closed
    body["coverage"]["max_date"] = extend_to.isoformat()
    # minor bump (신규 연도 추가 — schema version 규약).
    major, minor, patch = body["version"].split(".")
    body["version"] = f"{major}.{int(minor) + 1}.0"
    body["verified_at"] = kst_today().isoformat()
    body["verified_by"] = "speculum-pykrx-derived"
    body["verified_source"] = (
        "2024: KRX 공식 캘린더 수동 검증; "
        "2025+: pykrx get_market_ohlcv(005930) 거래일 도출 (keyless). "
        "https://open.krx.co.kr / pykrx"
    )
    # content_hash 는 자기 자신 제외 후 재계산되므로 placeholder 로 두고 _fill_content_hash.
    body["content_hash"] = "sha256:0"
    _DATA_PATH.write_text(
        json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    new_hash = _fill_content_hash(_DATA_PATH)
    print(
        f"확장 완료: coverage → {extend_to}, 신규 휴장 {len(new_closed)}건, "
        f"version {body['version']}, hash {new_hash[:20]}...",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="KRX 캘린더 pykrx 도출 확장.")
    parser.add_argument(
        "--to", type=date.fromisoformat, default=None,
        help="확장 종료일 (YYYY-MM-DD). 기본 = 오늘(KST).",
    )
    args = parser.parse_args()
    run(extend_to=args.to or kst_today())


if __name__ == "__main__":
    main()

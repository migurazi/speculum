"""Meta endpoint — `/api/as_of` (정규화 결과) + `/api/policy-versions` (정책 freeze).

본 사이클 (T24) 의 deliverable. Frontend picker / 디버깅 / Save Run freeze
검증의 source.

설계 원칙 (oracle 자문 결정 6):
- 두 endpoint 분리 — 책임·호출 빈도·cache 주기 다름.
- `/api/as_of` = picker click 마다 호출 (저 빈도).
- `/api/policy-versions` = 디버깅 / Save Run 시 freeze 검증 (매우 저 빈도).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from app.api.dependencies import NormalizedAsOfDep
from app.api.dependencies.repositories import ActivePackDep, BatchRunRepoDep, PriceRepoDep
from app.schemas.data_freshness import DataFreshnessOut
from app.services.data_freshness import assess_data_freshness
from app.services.krx_calendar import DEFAULT_CALENDAR
from app.services.snapshot_versions import collect_active_policy_versions

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/as_of")
async def get_as_of(as_of: NormalizedAsOfDep) -> dict:
    """현재 입력의 정규화 결과 반환. picker UI 가 snap 결과 미리 보기 + Response
    header (`X-AsOf-*`) 와 함께 사용.

    Response body:
        value: 정규화된 영업일 (ISO 8601).
        was_defaulted: 입력 None → kst_today 채움.
        was_snapped: 휴장일 입력 → 직전 영업일 snap.
        original_input: snap 전 입력 (was_snapped=True 시).
        pit_policy_version: 본 결과를 만든 정책 버전.

    Response headers (`as_of` dependency 가 자동 채움):
        X-AsOf, X-AsOf-Defaulted, X-AsOf-Snapped, X-AsOf-Original.
    """
    return {
        "value": as_of.value.isoformat(),
        "was_defaulted": as_of.was_defaulted,
        "was_snapped": as_of.was_snapped,
        "original_input": (
            as_of.original_input.isoformat()
            if as_of.original_input is not None else None
        ),
        "pit_policy_version": as_of.pit_policy_version,
    }


@router.get("/policy-versions")
async def get_policy_versions() -> dict:
    """현재 활성 정책의 hash + version 집계 — Screen Run snapshot freeze 입력의 source.

    8 기둥 §2.10 Reproducibility 의 인스턴스. 6 개월 후 같은 Run 재현 시 본
    endpoint 결과와 snapshot 의 `data_versions` 비교로 변경된 정책 식별.

    Response 는 `snapshot_versions.collect_active_policy_versions()` 의 결과
    (14 키 Mapping[str, str] — M2 T72 distribution_policy_version, M7 #5
    total_return_policy_hash / total_return_policy_version 합류 반영).
    """
    return dict(collect_active_policy_versions())


@router.get("/factors")
async def get_factors(pack: ActivePackDep) -> dict:
    """활성 factor pack 의 factor 목록 — Screener 의 factor 선택 UI source.

    frontend 가 canonical_id 를 직접 타이핑하지 않고 드롭다운으로 고를 수 있도록
    `(canonical_id, name, unit, tags)` 를 노출. canonical_id 는 조건/표시 입력의
    실제 값, name 은 사람이 읽는 라벨, tags 는 카테고리 그룹핑용(valuation 등).

    Response body:
        pack_slug / pack_version: 활성 pack 식별 (UI 캐시 무효화 키).
        factors: `[{canonical_id, name, unit, tags}]` — pack 정의 순서.
    """
    factors = [
        {
            "canonical_id": f["canonical_id"],
            "name": f["name"],
            "unit": f["unit"],
            "tags": list(f.get("tags", ())),
        }
        for f in pack.body["factors"]
    ]
    return {
        "pack_slug": pack.pack_slug,
        "pack_version": pack.version,
        "factors": factors,
    }


@router.get("/calendar")
async def get_calendar(price_repo: PriceRepoDep) -> dict:
    """검증된 KRX 캘린더 coverage + 적재된 최신 거래일 — Frontend as_of 클램프 source.

    클라이언트가 picker/store 의 as_of 를 본 범위로 자동 보정해 범위 밖
    400(AS_OF_OUT_OF_RANGE)을 사전 차단한다. earliest/latest_business_day 는
    경계 휴장일(2024-01-01 신정·2024-12-31 연말 휴장)을 snap 한 영업일이라,
    클라이언트가 이 값으로 클램프하면 server normalize 가 추가 snap·400 없이 통과.
    latest_data_date 는 실제 적재된 최신 거래일(실데이터 단일 경로) — 클램프
    상한의 우선 목표(데이터 없는 미래/공백일 회피). 데이터 부재 시 null.
    """
    cal = DEFAULT_CALENDAR
    latest_data = price_repo.fetch_latest_trade_date()
    return {
        "min_date": cal.min_date.isoformat(),
        "max_date": cal.max_date.isoformat(),
        "earliest_business_day": cal.snap_to_next(cal.min_date).isoformat(),
        "latest_business_day": cal.snap_to_previous(cal.max_date).isoformat(),
        "latest_data_date": latest_data.isoformat() if latest_data is not None else None,
        "version": cal.version,
        "content_hash": cal.content_hash,
    }


@router.get("/data-freshness", response_model=DataFreshnessOut)
async def get_data_freshness(
    batch_run_repo: BatchRunRepoDep,
) -> DataFreshnessOut:
    """source(KRX/DART)별 최신 성공 batch 시각 + stale 여부 — M5 #4a (ADR-0033 D6).

    8 기둥 §2.1 Fidelity — "데이터 기준일 + stale 여부" 를 사실로 고지. 판정·해석
    문구 없음 (§2.2 No Advice / §2.7 Observation) — `is_stale` bool·경과 일수만.
    기존 공개 GET 패턴(`/api/policy-versions`·`/api/factors`)과 동일 — 인증 없음.

    `now` 는 서버 현재 UTC(`datetime.now(UTC)`)를 주입 — 서비스(`assess_data_
    freshness`)는 결정성 위해 `datetime.now()` 를 직접 호출하지 않는다. calendar 는
    검증된 KRX 캘린더(`DEFAULT_CALENDAR`) — KRX 영업일 경과(stale 판정)에 사용.

    stale 임계 (ADR-0033 D6): KRX 3 영업일 초과 / DART 100 calendar days 초과
    (분기 공시 주기 근사, 재튜닝 가능).
    """
    freshness = assess_data_freshness(
        now=datetime.now(UTC),
        batch_run_repo=batch_run_repo,
        calendar=DEFAULT_CALENDAR,
    )
    return DataFreshnessOut.from_domain(freshness)

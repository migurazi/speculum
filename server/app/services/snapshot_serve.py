"""Precompute snapshot serve — 공유 순수 predicate (ⓓ read-bypass).

market_overview(slice 2b)와 screen(slice 1b)이 공유하는 **단일 serve predicate**.
한 종목의 요청 factor 집합 전체를 precompute snapshot 으로 serve 할 수 있는지
(all-or-nothing) 판정하고, 가능하면 `{factor_uuid: value}` 를 반환한다.

설계 (oracle 설계검토 결정 2 — 공유 helper 추출):
    market_overview `_serve_overview_from_snapshot` 의 반환계약(value dict)과
    screen 의 반환계약(조건 통과 bool)이 달라 동일 helper 로 합치면 모듈 결합이
    생긴다. 대신 **가장 낮은 공통분모 = `{uuid: value} | None`** 를 본 모듈이
    제공하고, 호출자가 각자 재매핑(market: uuid→fid 집계, screen: 조건 비교)한다.
    1a harness 도 본 helper 만 import → predicate drift 0.

predicate (all-or-nothing per code):
    1. snapshot_repo 또는 current_data_versions 가 None(미주입 = Fake/세션없음)
       → None(호출자가 live fallback).
    2. 요청 factor_uuids 중 **하나라도** snapshot 에 부재(miss) → None(부분 serve
       금지 — 전체 live).
    3. 각 snapshot 의 `dict(snap.data_versions) != current_data_versions`(정책/
       batch 세대 갱신 = stale) → None(전체 live, recompute).
    4. 전 uuid hit + 전부 data_versions 일치 → `{uuid: value(Decimal | None)}`.
       value None 은 미산정(live 의 is_na 와 동일 의미) — 호출자가 보존 처리.

freshness 보장은 data_versions 에 내장된 krx/dart/dividend batch_id + 정책 hash
가 담당(새 배치 → batch_id 변경 → equality mismatch → stale → live). pack 세대
(factor_pack_content_hash)도 data_versions 에 있어 custom/다른 pack 은 자동 stale
→ live(C1, 호출자가 current_data_versions 를 pack-aware 로 계산). dividend 정정
known-limit(dividend_batch_id 가 항상 ""라 못 잡음)은 호출자가 dividend-의존 factor
를 serve 대상에서 제외(C2-a)하여 회피 — 본 helper 는 순수 predicate 라 그 정책을
모른다(호출자 책임).

본 helper 는 value 만 사용 — snapshot 의 inputs/citation 은 serve 에 불요
(market: 집계, screen: 조건 비교 모두 value 만 필요).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from uuid import UUID

from app.repositories.pit_protocols import StockSnapshotRepository


def try_serve_snapshot_values(
    code: str,
    *,
    as_of: date,
    factor_uuids: frozenset[UUID],
    snapshot_repo: StockSnapshotRepository | None,
    current_data_versions: Mapping[str, str] | None,
) -> dict[UUID, Decimal | None] | None:
    """한 종목의 요청 factor 전체를 snapshot 으로 serve 가능하면 `{uuid: value}`.

    all-or-nothing — 요청 factor_uuids 가 모두 hit 하고 각 snapshot 의
    data_versions 가 현재 fingerprint 와 일치할 때만 dict 반환. 하나라도
    miss/stale 이거나 repo/dv 미주입이면 None(호출자가 live 평가).

    Args:
        code: 종목코드.
        as_of: PIT 기준 일자 (snapshot exact-match lookup 키).
        factor_uuids: serve 하려는 factor uuid 집합 (조건 factor / overview factor).
        snapshot_repo: precompute lookup repository. None 이면 None 반환.
        current_data_versions: 현재 live freeze fingerprint(pack-aware). None 이면
            None 반환. snapshot.data_versions 와 byte-equality 로 serve-vs-recompute
            판정.

    Returns:
        전 factor serve 가능 시 `{factor_uuid: value(Decimal | None=미산정)}`,
        아니면 None(부분 serve 안 함 — 전체 live).
    """
    if snapshot_repo is None or current_data_versions is None:
        return None
    snaps = snapshot_repo.fetch_snapshots_for_code(
        code, as_of=as_of, factor_uuids=factor_uuids,
    )
    by_uuid = {s.factor_uuid: s for s in snaps}
    served: dict[UUID, Decimal | None] = {}
    for uuid in factor_uuids:
        snap = by_uuid.get(uuid)
        if snap is None:
            return None  # 일부 factor miss → 전체 live(부분 serve 안 함).
        if dict(snap.data_versions) != current_data_versions:
            return None  # stale(정책/batch 세대 갱신) → 전체 live(재계산).
        served[uuid] = snap.value
    return served

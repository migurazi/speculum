"""V1 관통 e2e — 캡처 실 DART 재무 → 변환 → 실 SQLite → 스크리너 EPS 실값 (ROADMAP_v2 V1).

V0(`test_v0_chart_e2e.py`)가 실 OHLCV 로 "차트가 비춘다"를 잠갔듯, 본 테스트는
ROADMAP_v2 §3 의 V1 acceptance gate("스크리너가 실값을 낸다 = 실데이터 e2e ≥1")를
잠근다:

  실제 DART 응답을 캡처한 fixture(`tests/fixtures/captured/dart_financials_005930.json`,
  삼성전자 trailing 5분기 연결 재무 — 손제작 아님)
  → **프로덕션 변환기**(`_convert_to_financial_records`)로 FinancialRecord 화
  → **실 in-memory SQLite**(SqlFinancialRepository, Fake 아님)
  → `screen_active_codes`(EPS TTM 조건) → **005930 ≥1 반환** 검증.

핵심 — 기존 단위/read-bypass 테스트는 EPS 값을 `each = 1000 + i*500` 식 **합성값**으로
seed 해 consumer 를 자기검증(tautology)했다. 본 테스트는 값을 **실 DART 캡처**에서
유도하므로 tautology 가 아니다. B1 누적(YTD)→standalone 차분이 실데이터에서 옳게
동작하는지까지 관통 검증:
    실 basic_eps YTD = [206, 228, 810, 2131](2023Q1~Q4) + 975(2024Q1)
    standalone 차분 = [206, 22, 582, 1321, 975]
    TTM(trailing 4Q @ 2024Q1 공시후) = 22+582+1321+975 = **2900**(실 삼성전자 EPS).

외부 네트워크 호출 0(캡처본 재생). fixture 갱신은 `scripts.capture_dart_fixture`.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from app.adapters.base import FinancialStatementRow, IfrsType
from app.api.routes.screen import screen_active_codes
from app.db.converters import citation_record_to_orm, stocks_master_record_to_orm
from app.db.orm.batch_runs import BATCH_STATUS_SUCCESS, BatchRunORM
from app.models.source_citation import SourceCitation, SourceKind
from app.repositories.pit_protocols import CodeHistoryEntry, StockMasterRecord
from app.repositories.sql_repositories import (
    SqlCorporateActionRepository,
    SqlFinancialRepository,
    SqlMarketCapRepository,
    SqlPriceRepository,
    SqlStocksMasterRepository,
    SqlTreasurySharesRepository,
)
from app.schemas.screen import ConditionIn, OpEnum
from app.services.factor_evaluator import FactorEvaluator
from app.services.factor_pack import DEFAULT_PACK
from app.services.lineage import lineage_id_for_code
from batch.dart_daily import _convert_to_financial_records

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures" / "captured" / "dart_financials_005930.json"
)
_CODE = "005930"
# 캡처 fixture 의 2024Q1 공시(effective_date ~2024-05) 이후라야 5분기 전부 가시 →
# TTM 성립. 실데이터 최신 거래일 기준.
_AS_OF = date(2024, 6, 28)
_EPS_FACTOR_ID = "eps:basic-ttm-consolidated-ifrs"
_CITATION = UUID("00000000-0000-0000-0000-00000000eee1")
_BATCH = UUID("00000000-0000-0000-0000-0000000000e1")
# 모집단에 재무 결손 종목 2개 추가 — na_excluded(정직 제외) 검증용.
_ABSENT = ("000001", "000002")


def _load_captured_rows() -> tuple[FinancialStatementRow, ...]:
    """캡처 fixture JSON → FinancialStatementRow tuple(adapter 출력 형태 복원)."""
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return tuple(
        FinancialStatementRow(
            code=r["code"],
            fiscal_year=r["fiscal_year"],
            fiscal_quarter=r["fiscal_quarter"],
            effective_date=date.fromisoformat(r["effective_date"]),
            account=r["account"],
            value=Decimal(r["value"]),
            unit=r["unit"],
            ifrs_type=IfrsType(r["ifrs_type"]),  # "consolidated" → IfrsType.CFS.
            rcept_no=r["rcept_no"],
            currency=r["currency"],
            effective_date_precise=r["effective_date_precise"],
        )
        for r in payload["rows"]
    )


def _seed_master_and_citation(session: Session) -> None:
    """batch_run + DART citation + stocks_master(005930 + 결손 2종) — FK 순서 flush."""
    session.add(BatchRunORM(
        id=_BATCH, market=None, source="DART",
        started_at=datetime(2024, 5, 16, 9, 0, tzinfo=UTC),
        ended_at=datetime(2024, 5, 16, 9, 5, tzinfo=UTC),
        success_count=1, status=BATCH_STATUS_SUCCESS,
    ))
    session.flush()
    session.add(citation_record_to_orm(SourceCitation(
        id=_CITATION, source=SourceKind.DART, identifier="20240516000001",
        retrieved_at=datetime(2024, 5, 16, 9, 0, tzinfo=UTC),
        effective_date=date(2024, 5, 16), adapter_version="1.0.0",
        batch_id=_BATCH, url=None,
        created_at=datetime(2024, 5, 16, 9, 0, tzinfo=UTC),
    )))
    session.flush()
    for code in (_CODE, *_ABSENT):
        session.add(stocks_master_record_to_orm(StockMasterRecord(
            id=lineage_id_for_code(code), current_code=code,
            current_name=f"stock-{code}", market="KOSPI",
            listing_date=date(2000, 1, 1), delisting_date=None, fiscal_month=12,
            code_history=(CodeHistoryEntry(
                code, date(2000, 1, 1), None, "initial_listing"),),
            ifrs_preference_default="AUTO",
        )))
    session.flush()


def _build_repos(session: Session) -> dict:
    return dict(
        stocks_repo=SqlStocksMasterRepository(session),
        price_repo=SqlPriceRepository(session),
        financial_repo=SqlFinancialRepository(session),
        corporate_action_repo=SqlCorporateActionRepository(session),
        market_cap_repo=SqlMarketCapRepository(session),
        treasury_repo=SqlTreasurySharesRepository(session),
    )


def test_v1_captured_dart_flows_to_screener_eps(db_session: Session) -> None:
    """캡처 실 DART 재무 → 변환 → 실 SQLite → EPS 스크리너가 005930 ≥1 반환.

    V1 acceptance — "스크리너가 실값을 낸다". 실 DB 경로(Fake 아님), 실 DART 캡처값.
    """
    rows = _load_captured_rows()
    assert rows, "캡처 fixture 가 비어있음 — ground truth 부재"
    # ground truth 확인 — fixture 가 005930 의 5분기 basic_eps 를 담는다(차분 TTM 전제).
    eps_periods = sorted({
        (r.fiscal_year, r.fiscal_quarter) for r in rows if r.account == "basic_eps"
    })
    assert eps_periods == [(2023, 1), (2023, 2), (2023, 3), (2023, 4), (2024, 1)], (
        f"basic_eps 5분기 연속 필요(B1 standalone) — got {eps_periods}"
    )

    _seed_master_and_citation(db_session)

    # 프로덕션 변환기로 FinancialStatementRow → FinancialRecord(account 매핑·
    # fiscal_period·lineage 동일 경로) → 실 SqlFinancialRepository 적재.
    records = _convert_to_financial_records(rows=rows, citation_id=_CITATION)
    SqlFinancialRepository(db_session).save_financials(records)
    db_session.commit()  # DEFERRABLE FK(citation) 최종 검사.

    # EPS TTM > 1000 — 실 삼성전자 TTM EPS=2900 통과, 재무 결손 2종은 N/A 제외.
    conditions = [ConditionIn(factor=_EPS_FACTOR_ID, op=OpEnum.GT, value="1000")]
    result = screen_active_codes(
        as_of=_AS_OF, conditions=conditions, pack=DEFAULT_PACK,
        evaluator=FactorEvaluator(),
        **_build_repos(db_session),
    )

    # AC: 실데이터 e2e ≥1 — 005930 이 실 EPS 로 조건 통과.
    assert _CODE in result.result_codes, (
        f"005930 미반환 — TTM EPS 실값이 스크리너를 관통 못함. got={result.result_codes}"
    )
    assert result.result_codes == (_CODE,)  # 결손 2종은 매칭 0(정직 제외).
    # 모집단 3 중 재무 결손 2종이 na_excluded 로 정직 집계(S3 투명성, §2.1 Fidelity).
    assert result.universe_size == 3
    assert result.na_excluded_count == len(_ABSENT)


def test_v1_screener_excludes_when_threshold_above_real_eps(db_session: Session) -> None:
    """조건 임계가 실 EPS(2900) 초과면 005930 도 탈락 — 합성 아닌 실값 경계 검증.

    fixture 가 합성이면 임의 임계로 통과시킬 수 있으나, 실 EPS=2900 이 ground truth
    라 임계 3000 에서는 반드시 0건이어야 한다(실값 경계의 tautology-free 증거)."""
    rows = _load_captured_rows()
    _seed_master_and_citation(db_session)
    SqlFinancialRepository(db_session).save_financials(
        _convert_to_financial_records(rows=rows, citation_id=_CITATION),
    )
    db_session.commit()

    conditions = [ConditionIn(factor=_EPS_FACTOR_ID, op=OpEnum.GT, value="3000")]
    result = screen_active_codes(
        as_of=_AS_OF, conditions=conditions, pack=DEFAULT_PACK,
        evaluator=FactorEvaluator(),
        **_build_repos(db_session),
    )
    # 실 TTM EPS=2900 < 3000 → 005930 탈락. 합성이면 못 잡을 실값 경계.
    assert _CODE not in result.result_codes
    assert result.result_codes == ()

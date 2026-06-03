/**
 * Portfolio API client — `/api/portfolio` 거래내역 + 포지션 CRUD.
 *
 * Backend `app/api/routes/portfolio.py` 의 wire schema 와 1:1 매핑.
 * snake_case ↔ camelCase 변환은 notes.ts 패턴 동일.
 *
 * 인증:
 *   fetchJson 이 Authorization Bearer 헤더 자동 전송(setAuthToken 주입).
 *   user-scoped(USER_PRIVATE) — 본인 거래내역/포지션만 접근 (ADR-0029 D4).
 *   미인증(401) 시 ApiError(status=401) throw — 호출자가 로그인 유도 처리.
 *
 * 가드레일 (ADR-0029 D3 / 8기둥 §2.2 No Advice):
 *   - 손익률 필드 없음: 손익은 realizedPnl/unrealizedPnl 사실 숫자만.
 *   - 등급/순위/평가 필드 없음.
 *   - 세금 필드 없음(D6 세전만).
 *
 * 관련:
 *   - ADR-0029 (Portfolio 회계 — D1 거래, D2 포지션, D3 회계≠평가, D4 USER_PRIVATE, D6 세전)
 *   - ADR-0020 (append-only invariant)
 *   - M3_PLAN #4
 */

import { fetchJson } from "./client";

// =============================================================================
// Wire types (backend snake_case)
// =============================================================================

interface PortfolioTransactionWire {
  readonly id: string;
  readonly code_lineage_id: string;
  readonly side: "buy" | "sell";
  readonly quantity: number;
  readonly unit_price: string;  // Decimal → string
  readonly trade_date: string;  // "YYYY-MM-DD"
  readonly fee: string;         // Decimal → string
  readonly created_at: string;  // ISO 8601
}

interface PortfolioTransactionListWire {
  readonly transactions: ReadonlyArray<PortfolioTransactionWire>;
}

interface PortfolioPositionWire {
  readonly code_lineage_id: string;
  readonly quantity: number;
  readonly avg_cost: string;           // Decimal → string
  readonly total_cost: string;         // Decimal → string
  readonly realized_pnl: string;       // Decimal → string (세전, ADR-0029 D6)
  readonly market_value: string | null;    // Decimal → string | null (현재가 있을 때만)
  readonly unrealized_pnl: string | null;  // Decimal → string | null (현재가 있을 때만)
}

interface PortfolioPositionListWire {
  readonly positions: ReadonlyArray<PortfolioPositionWire>;
}

// =============================================================================
// Domain types (camelCase) — export
// =============================================================================

/**
 * 거래내역 단건 — append-only (ADR-0020).
 * 손익률/등급/순위 필드 없음 (ADR-0029 D3).
 */
export interface PortfolioTransaction {
  readonly id: string;
  readonly codeLineageId: string;
  readonly side: "buy" | "sell";
  readonly quantity: number;
  readonly unitPrice: string;   // Decimal string
  readonly tradeDate: string;   // "YYYY-MM-DD"
  readonly fee: string;         // Decimal string
  readonly createdAt: string;   // ISO 8601
}

/**
 * 종목별 포지션 — 거래내역 집계 결과 (ADR-0029 D2).
 * 손익률/등급/순위 필드 0 (ADR-0029 D3).
 * 세금 필드 0 — 세전만 (ADR-0029 D6).
 */
export interface PortfolioPosition {
  readonly codeLineageId: string;
  readonly quantity: number;
  readonly avgCost: string;          // 평단 (이동평균법)
  readonly totalCost: string;        // 원가 (취득가액 합)
  readonly realizedPnl: string;      // 실현손익 (세전, ADR-0029 D6)
  readonly marketValue: string | null;    // 평가금액 (현재가×수량, null=현재가 없음)
  readonly unrealizedPnl: string | null;  // 평가손익 (null=현재가 없음)
}

// =============================================================================
// Mapping
// =============================================================================

function wireToTransaction(w: PortfolioTransactionWire): PortfolioTransaction {
  return {
    id: w.id,
    codeLineageId: w.code_lineage_id,
    side: w.side,
    quantity: w.quantity,
    unitPrice: w.unit_price,
    tradeDate: w.trade_date,
    fee: w.fee,
    createdAt: w.created_at,
  };
}

function wireToPosition(w: PortfolioPositionWire): PortfolioPosition {
  return {
    codeLineageId: w.code_lineage_id,
    quantity: w.quantity,
    avgCost: w.avg_cost,
    totalCost: w.total_cost,
    realizedPnl: w.realized_pnl,
    marketValue: w.market_value,
    unrealizedPnl: w.unrealized_pnl,
  };
}

// =============================================================================
// API 함수 — AddTransaction 파라미터
// =============================================================================

export interface AddTransactionParams {
  readonly codeLineageId: string;
  readonly side: "buy" | "sell";
  readonly quantity: number;
  readonly unitPrice: string;  // Decimal string
  readonly tradeDate: string;  // "YYYY-MM-DD"
  readonly fee: string;        // Decimal string
}

// =============================================================================
// API functions
// =============================================================================

/**
 * 거래 추가.
 * POST /api/portfolio/transactions
 * body: { code_lineage_id, side, quantity, unit_price, trade_date, fee }
 * 201 → PortfolioTransaction. 인증 필요 → 401 on miss.
 */
export async function addTransaction(
  params: AddTransactionParams,
  signal?: AbortSignal,
): Promise<PortfolioTransaction> {
  const wire = await fetchJson<PortfolioTransactionWire>(
    "/api/portfolio/transactions",
    {
      method: "POST",
      body: {
        code_lineage_id: params.codeLineageId,
        side: params.side,
        quantity: params.quantity,
        unit_price: params.unitPrice,
        trade_date: params.tradeDate,
        fee: params.fee,
      },
      signal,
    },
  );
  return wireToTransaction(wire);
}

/**
 * 거래내역 목록 조회.
 * GET /api/portfolio/transactions
 * 200 → PortfolioTransaction[]. 인증 필요 → 401 on miss.
 */
export async function listTransactions(
  signal?: AbortSignal,
): Promise<ReadonlyArray<PortfolioTransaction>> {
  // backend 응답: { transactions: [...] } 또는 배열 — 두 형태 모두 처리.
  const raw = await fetchJson<
    PortfolioTransactionListWire | ReadonlyArray<PortfolioTransactionWire>
  >("/api/portfolio/transactions", { method: "GET", signal });

  const wireList: ReadonlyArray<PortfolioTransactionWire> = Array.isArray(raw)
    ? raw
    : raw.transactions;

  return wireList.map(wireToTransaction);
}

/**
 * 거래 삭제 (역분개용 — append-only 정신 유지, ADR-0020).
 * DELETE /api/portfolio/transactions/{tx_id}
 * 204. 인증 필요 → 401 on miss.
 */
export async function deleteTransaction(
  txId: string,
  signal?: AbortSignal,
): Promise<void> {
  await fetchJson<void>(
    `/api/portfolio/transactions/${encodeURIComponent(txId)}`,
    { method: "DELETE", signal },
  );
}

/**
 * 포지션 목록 조회.
 * GET /api/portfolio/positions?as_of=YYYY-MM-DD
 * 200 → PortfolioPosition[]. 인증 필요 → 401 on miss.
 * 손익률/등급/순위 없음 (ADR-0029 D3). 세금 없음 (D6).
 */
export async function listPositions(
  asOf: string,
  signal?: AbortSignal,
): Promise<ReadonlyArray<PortfolioPosition>> {
  const raw = await fetchJson<
    PortfolioPositionListWire | ReadonlyArray<PortfolioPositionWire>
  >("/api/portfolio/positions", {
    method: "GET",
    searchParams: { as_of: asOf },
    signal,
  });

  const wireList: ReadonlyArray<PortfolioPositionWire> = Array.isArray(raw)
    ? raw
    : raw.positions;

  return wireList.map(wireToPosition);
}

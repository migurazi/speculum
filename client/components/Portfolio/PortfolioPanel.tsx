"use client";

/**
 * PortfolioPanel — 거래내역 입력/목록 + 포지션 표 (ADR-0029).
 *
 * 구조:
 *   - 탭: [거래내역] [포지션]
 *   - 거래내역 탭: 입력 폼 + 목록 (삭제)
 *   - 포지션 탭: 종목별 보유수량/평단/원가/실현손익/평가금액/평가손익 표
 *
 * 가드레일 (ADR-0029 D3 — 회계≠평가 분리, 절대 준수):
 *   - 손익(realizedPnl/unrealizedPnl) 헤드라인 대형 강조 금지.
 *     다른 셀과 동일 비중/중립 톤으로만 표시.
 *   - 등락색(녹수익/적손실) 절대 금지 — 양수/음수 부호로 색 분기 0.
 *     한국 관행 빨강/파랑도 금지 (§2.2 No Advice 위반 통로).
 *   - "수익 중"/"손실 중"/"양호"/"수익률 N%" 평가 라벨·등급·별점 0.
 *   - 손익 순위/정렬 default 0 — code_lineage_id asc 결정적 정렬 유지.
 *   - "더 담기"/"비중 조절" 행동 유도 0.
 *   - 세전 표시 (D6) — 세금 필드 없음.
 *   - 톤: grayscale(neutral) 단일. 판단색 0.
 *
 * USER_PRIVATE scope (ADR-0029 D4):
 *   본인 자산 → 인증 필수. 401 시 로그인 유도(CustomScreenPanel 패턴).
 *
 * 색 명명 상수 (PORTFOLIO_PANEL_*):
 *   모두 neutral/grayscale 계열만 — portfolio-gate.test.tsx 가 검증한다.
 *
 * 관련:
 *   - lib/api/portfolio.ts — API client (camelCase).
 *   - ADR-0029 D1~D6, 8기둥 §0/§2.2/§2.9.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { signIn } from "next-auth/react";
import { useTranslations } from "next-intl";
import { useRef, useState, type KeyboardEvent } from "react";

import { ApiError } from "@/lib/api/client";
import {
  addTransaction,
  deleteTransaction,
  listPositions,
  listTransactions,
  type AddTransactionParams,
  type PortfolioPosition,
  type PortfolioTransaction,
} from "@/lib/api/portfolio";
import { useAsOfStore } from "@/state/as-of-store";
import { cn } from "@/lib/utils";

// =============================================================================
// No Advice 시각 상수 — grayscale(neutral) 계열만.
// portfolio-gate.test.tsx 가 이 상수를 검증한다 (ADR-0029 D3).
// 등락색(red/green/blue) 0, 판단색 0.
// =============================================================================

/**
 * 포지션 표 셀 기본 톤 — grayscale.
 * 손익 셀도 이 상수와 동일 톤 사용 — 양수/음수 색 분기 절대 금지.
 */
export const PORTFOLIO_PANEL_CELL_TEXT = "text-neutral-800";

/**
 * 포지션 표 헤더 톤 — grayscale.
 */
export const PORTFOLIO_PANEL_HEADER_TEXT = "text-neutral-600";

/**
 * 포지션 표 행 배경 기본(홀수) 톤 — 판단색 아님.
 */
export const PORTFOLIO_PANEL_ROW_BG = "bg-white";

/**
 * 포지션 표 행 배경 줄무늬(짝수) 톤 — grayscale 가독성용.
 */
export const PORTFOLIO_PANEL_ROW_STRIPE = "bg-neutral-50";

/**
 * 손익 셀 톤 — 동일 grayscale. 양수/음수 분기 0.
 * "성과 평가" 통로 차단 (ADR-0029 D3 / §2.2 No Advice).
 */
export const PORTFOLIO_PANEL_PNL_TEXT = "text-neutral-800";

// =============================================================================
// 401 여부 판단 헬퍼
// =============================================================================

function isUnauthorized(err: Error): boolean {
  return err instanceof ApiError && err.status === 401;
}

// =============================================================================
// 폼 상태 타입
// =============================================================================

interface TxFormState {
  codeLineageId: string;
  side: "buy" | "sell";
  quantity: string;
  unitPrice: string;
  tradeDate: string;
  fee: string;
}

const EMPTY_FORM: TxFormState = {
  codeLineageId: "",
  side: "buy",
  quantity: "",
  unitPrice: "",
  tradeDate: "",
  fee: "0",
};

// =============================================================================
// 탭 타입
// =============================================================================

type ActiveTab = "transactions" | "positions";

// WAI-ARIA tabs — 탭 순서(화살표/Home/End 네비)와 라벨 i18n 키 매핑. tablist /
// tab / tabpanel 의 id 도 본 키로 파생(portfolio-tab-*, portfolio-panel-*).
const TAB_ORDER: readonly ActiveTab[] = ["transactions", "positions"];
const TAB_LABEL_KEY: Readonly<Record<ActiveTab, string>> = {
  transactions: "tabTransactions",
  positions: "tabPositions",
};

// =============================================================================
// 거래 입력 폼
// =============================================================================

function TransactionForm({
  onSubmit,
  isPending,
  t,
}: {
  onSubmit: (params: AddTransactionParams) => void;
  isPending: boolean;
  t: ReturnType<typeof useTranslations<"portfolio">>;
}): JSX.Element {
  const [form, setForm] = useState<TxFormState>(EMPTY_FORM);
  const [validationError, setValidationError] = useState<string | null>(null);

  function updateField<K extends keyof TxFormState>(
    field: K,
    value: TxFormState[K],
  ): void {
    setForm((prev) => ({ ...prev, [field]: value }));
    setValidationError(null);
  }

  function validate(): string | null {
    if (form.codeLineageId.trim().length === 0) {
      return t("addTransaction.validationCodeRequired");
    }
    const qty = parseInt(form.quantity, 10);
    if (!Number.isInteger(qty) || qty < 1) {
      return t("addTransaction.validationQuantityInvalid");
    }
    const price = parseFloat(form.unitPrice);
    if (!isFinite(price) || price <= 0) {
      return t("addTransaction.validationUnitPriceInvalid");
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(form.tradeDate.trim())) {
      return t("addTransaction.validationTradeDateInvalid");
    }
    const fee = parseFloat(form.fee);
    if (!isFinite(fee) || fee < 0) {
      return t("addTransaction.validationFeeInvalid");
    }
    return null;
  }

  function handleSubmit(): void {
    const err = validate();
    if (err !== null) {
      setValidationError(err);
      return;
    }
    onSubmit({
      codeLineageId: form.codeLineageId.trim(),
      side: form.side,
      quantity: parseInt(form.quantity, 10),
      unitPrice: form.unitPrice.trim(),
      tradeDate: form.tradeDate.trim(),
      fee: form.fee.trim(),
    });
    setForm(EMPTY_FORM);
    setValidationError(null);
  }

  return (
    <section
      aria-labelledby="add-tx-heading"
      className="rounded-lg border border-neutral-200 bg-neutral-50 p-4 space-y-3"
    >
      <h3
        id="add-tx-heading"
        className="text-sm font-medium text-neutral-700"
      >
        {t("addTransaction.heading")}
      </h3>

      {/* 입력 필드 그리드 */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {/* 종목 lineage ID */}
        <div className="col-span-2 sm:col-span-3 space-y-1">
          <label
            htmlFor="portfolio-lineage-id"
            className="block text-xs text-neutral-600"
          >
            {t("addTransaction.codeLineageIdLabel")}
          </label>
          <input
            id="portfolio-lineage-id"
            type="text"
            value={form.codeLineageId}
            onChange={(e) => updateField("codeLineageId", e.target.value)}
            placeholder={t("addTransaction.codeLineageIdPlaceholder")}
            className="w-full rounded border border-neutral-300 px-2 py-1 font-mono text-xs text-neutral-800 placeholder:text-neutral-400"
            disabled={isPending}
          />
        </div>

        {/* 구분 (buy/sell) */}
        <div className="space-y-1">
          <label
            htmlFor="portfolio-side"
            className="block text-xs text-neutral-600"
          >
            {t("addTransaction.sideLabel")}
          </label>
          <select
            id="portfolio-side"
            value={form.side}
            onChange={(e) => updateField("side", e.target.value as "buy" | "sell")}
            className="w-full rounded border border-neutral-300 bg-white px-2 py-1 text-xs text-neutral-800"
            disabled={isPending}
          >
            <option value="buy">{t("addTransaction.sideBuy")}</option>
            <option value="sell">{t("addTransaction.sideSell")}</option>
          </select>
        </div>

        {/* 수량 */}
        <div className="space-y-1">
          <label
            htmlFor="portfolio-quantity"
            className="block text-xs text-neutral-600"
          >
            {t("addTransaction.quantityLabel")}
          </label>
          <input
            id="portfolio-quantity"
            type="text"
            inputMode="numeric"
            value={form.quantity}
            onChange={(e) => updateField("quantity", e.target.value)}
            placeholder={t("addTransaction.quantityPlaceholder")}
            className="w-full rounded border border-neutral-300 px-2 py-1 font-mono text-xs text-neutral-800 placeholder:text-neutral-400"
            disabled={isPending}
          />
        </div>

        {/* 단가 */}
        <div className="space-y-1">
          <label
            htmlFor="portfolio-unit-price"
            className="block text-xs text-neutral-600"
          >
            {t("addTransaction.unitPriceLabel")}
          </label>
          <input
            id="portfolio-unit-price"
            type="text"
            inputMode="decimal"
            value={form.unitPrice}
            onChange={(e) => updateField("unitPrice", e.target.value)}
            placeholder={t("addTransaction.unitPricePlaceholder")}
            className="w-full rounded border border-neutral-300 px-2 py-1 font-mono text-xs text-neutral-800 placeholder:text-neutral-400"
            disabled={isPending}
          />
        </div>

        {/* 거래일 */}
        <div className="space-y-1">
          <label
            htmlFor="portfolio-trade-date"
            className="block text-xs text-neutral-600"
          >
            {t("addTransaction.tradeDateLabel")}
          </label>
          <input
            id="portfolio-trade-date"
            type="date"
            value={form.tradeDate}
            onChange={(e) => updateField("tradeDate", e.target.value)}
            className="w-full rounded border border-neutral-300 px-2 py-1 text-xs text-neutral-800"
            disabled={isPending}
          />
        </div>

        {/* 수수료 */}
        <div className="space-y-1">
          <label
            htmlFor="portfolio-fee"
            className="block text-xs text-neutral-600"
          >
            {t("addTransaction.feeLabel")}
          </label>
          <input
            id="portfolio-fee"
            type="text"
            inputMode="decimal"
            value={form.fee}
            onChange={(e) => updateField("fee", e.target.value)}
            placeholder={t("addTransaction.feePlaceholder")}
            className="w-full rounded border border-neutral-300 px-2 py-1 font-mono text-xs text-neutral-800 placeholder:text-neutral-400"
            disabled={isPending}
          />
        </div>
      </div>

      {/* 유효성 오류 */}
      {validationError !== null ? (
        <p role="alert" className="text-xs text-neutral-700">
          {validationError}
        </p>
      ) : null}

      <button
        type="button"
        onClick={handleSubmit}
        disabled={isPending}
        data-testid="portfolio-add-tx-btn"
        className="rounded-md bg-neutral-900 px-4 py-2 text-sm font-medium text-white hover:bg-neutral-800 disabled:cursor-not-allowed disabled:bg-neutral-300"
      >
        {isPending
          ? t("addTransaction.submittingButton")
          : t("addTransaction.submitButton")}
      </button>
    </section>
  );
}

// =============================================================================
// 거래내역 목록
// =============================================================================

function TransactionList({
  transactions,
  onDelete,
  isDeleting,
  t,
}: {
  transactions: ReadonlyArray<PortfolioTransaction>;
  onDelete: (txId: string) => void;
  isDeleting: boolean;
  t: ReturnType<typeof useTranslations<"portfolio">>;
}): JSX.Element {
  if (transactions.length === 0) {
    return (
      <p className="text-sm text-neutral-500">{t("transactions.empty")}</p>
    );
  }

  return (
    <div
      data-testid="portfolio-tx-list"
      className="overflow-x-auto rounded-lg border border-neutral-200"
    >
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-neutral-200 bg-neutral-50">
            <th
              scope="col"
              className={cn("px-3 py-2 text-left font-medium", PORTFOLIO_PANEL_HEADER_TEXT)}
            >
              {t("transactions.columnDate")}
            </th>
            <th
              scope="col"
              className={cn("px-3 py-2 text-left font-medium", PORTFOLIO_PANEL_HEADER_TEXT)}
            >
              {t("transactions.columnSide")}
            </th>
            <th
              scope="col"
              className={cn("px-3 py-2 text-right font-medium", PORTFOLIO_PANEL_HEADER_TEXT)}
            >
              {t("transactions.columnQty")}
            </th>
            <th
              scope="col"
              className={cn("px-3 py-2 text-right font-medium", PORTFOLIO_PANEL_HEADER_TEXT)}
            >
              {t("transactions.columnUnitPrice")}
            </th>
            <th
              scope="col"
              className={cn("px-3 py-2 text-right font-medium", PORTFOLIO_PANEL_HEADER_TEXT)}
            >
              {t("transactions.columnFee")}
            </th>
            <th
              scope="col"
              className={cn("px-3 py-2 text-left font-medium", PORTFOLIO_PANEL_HEADER_TEXT)}
            >
              {t("transactions.columnLineage")}
            </th>
            <th scope="col" className="px-3 py-2">
              <span className="sr-only">{t("transactions.deleteButton")}</span>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-neutral-100">
          {transactions.map((tx, index) => (
            <tr
              key={tx.id}
              className={index % 2 === 0 ? PORTFOLIO_PANEL_ROW_BG : PORTFOLIO_PANEL_ROW_STRIPE}
            >
              <td className={cn("px-3 py-2 font-mono", PORTFOLIO_PANEL_CELL_TEXT)}>
                {tx.tradeDate}
              </td>
              <td className={cn("px-3 py-2", PORTFOLIO_PANEL_CELL_TEXT)}>
                {tx.side === "buy"
                  ? t("transactions.sideBuy")
                  : t("transactions.sideSell")}
              </td>
              <td className={cn("px-3 py-2 text-right font-mono", PORTFOLIO_PANEL_CELL_TEXT)}>
                {tx.quantity.toLocaleString()}
              </td>
              <td className={cn("px-3 py-2 text-right font-mono", PORTFOLIO_PANEL_CELL_TEXT)}>
                {tx.unitPrice}
              </td>
              <td className={cn("px-3 py-2 text-right font-mono", PORTFOLIO_PANEL_CELL_TEXT)}>
                {tx.fee}
              </td>
              <td className={cn("px-3 py-2 font-mono text-[11px]", PORTFOLIO_PANEL_CELL_TEXT)}>
                {tx.codeLineageId}
              </td>
              <td className="px-3 py-2 text-right">
                <button
                  type="button"
                  onClick={() => onDelete(tx.id)}
                  disabled={isDeleting}
                  className="text-neutral-400 hover:text-neutral-700 hover:underline disabled:cursor-not-allowed"
                >
                  {t("transactions.deleteButton")}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// =============================================================================
// 포지션 표
// =============================================================================

/**
 * 포지션 표 — ADR-0029 D3 가드레일 직접 적용.
 *
 * 손익(realizedPnl/unrealizedPnl) 셀:
 *   - PORTFOLIO_PANEL_PNL_TEXT (= "text-neutral-800") 단일 톤.
 *   - 양수/음수 분기로 색 변경 절대 금지.
 *   - 헤드라인 대형 강조 없음 — 다른 셀과 동일 font-size/weight.
 *   - "수익 중"/"손실 중" 라벨 없음.
 *
 * 순서: code_lineage_id asc 결정적 (backend 반환 순서 유지, 손익 정렬 default 0).
 */
function PositionTable({
  positions,
  asOf,
  t,
}: {
  positions: ReadonlyArray<PortfolioPosition>;
  asOf: string;
  t: ReturnType<typeof useTranslations<"portfolio">>;
}): JSX.Element {
  if (positions.length === 0) {
    return (
      <div className="space-y-2">
        <p className="text-xs text-neutral-500">
          {t("positions.asOfNote", { asOf })}
        </p>
        <p className="text-sm text-neutral-500">{t("positions.empty")}</p>
      </div>
    );
  }

  const naValue = t("positions.naValue");

  return (
    <div
      data-testid="portfolio-positions-table"
      className="space-y-2"
    >
      {/* 기준일 + 세전 안내 */}
      <p className="text-xs text-neutral-500">
        {t("positions.asOfNote", { asOf })}
      </p>
      {/* 세금 미포함 안내 (ADR-0029 D6) */}
      <p className="text-[11px] text-neutral-400">
        {t("positions.taxNote")}
      </p>

      <div className="overflow-x-auto rounded-lg border border-neutral-200">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-neutral-200 bg-neutral-50">
              <th
                scope="col"
                className={cn("px-3 py-2 text-left font-medium", PORTFOLIO_PANEL_HEADER_TEXT)}
              >
                {t("positions.columnLineage")}
              </th>
              <th
                scope="col"
                className={cn("px-3 py-2 text-right font-medium", PORTFOLIO_PANEL_HEADER_TEXT)}
              >
                {t("positions.columnQty")}
              </th>
              <th
                scope="col"
                className={cn("px-3 py-2 text-right font-medium", PORTFOLIO_PANEL_HEADER_TEXT)}
              >
                {t("positions.columnAvgCost")}
              </th>
              <th
                scope="col"
                className={cn("px-3 py-2 text-right font-medium", PORTFOLIO_PANEL_HEADER_TEXT)}
              >
                {t("positions.columnTotalCost")}
              </th>
              {/* 실현손익 — 다른 헤더와 동일 비중/톤. 강조 없음. */}
              <th
                scope="col"
                className={cn("px-3 py-2 text-right font-medium", PORTFOLIO_PANEL_HEADER_TEXT)}
              >
                {t("positions.columnRealizedPnl")}
              </th>
              <th
                scope="col"
                className={cn("px-3 py-2 text-right font-medium", PORTFOLIO_PANEL_HEADER_TEXT)}
              >
                {t("positions.columnMarketValue")}
              </th>
              {/* 평가손익 — 다른 헤더와 동일 비중/톤. 강조 없음. */}
              <th
                scope="col"
                className={cn("px-3 py-2 text-right font-medium", PORTFOLIO_PANEL_HEADER_TEXT)}
              >
                {t("positions.columnUnrealizedPnl")}
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-neutral-100">
            {positions.map((pos, index) => (
              <tr
                key={pos.codeLineageId}
                className={index % 2 === 0 ? PORTFOLIO_PANEL_ROW_BG : PORTFOLIO_PANEL_ROW_STRIPE}
              >
                <td className={cn("px-3 py-2 font-mono text-[11px]", PORTFOLIO_PANEL_CELL_TEXT)}>
                  {pos.codeLineageId}
                </td>
                <td className={cn("px-3 py-2 text-right font-mono", PORTFOLIO_PANEL_CELL_TEXT)}>
                  {pos.quantity.toLocaleString()}
                </td>
                <td className={cn("px-3 py-2 text-right font-mono", PORTFOLIO_PANEL_CELL_TEXT)}>
                  {pos.avgCost}
                </td>
                <td className={cn("px-3 py-2 text-right font-mono", PORTFOLIO_PANEL_CELL_TEXT)}>
                  {pos.totalCost}
                </td>
                {/* 실현손익 셀 — PORTFOLIO_PANEL_PNL_TEXT 단일 톤. 양수/음수 색 분기 0 (ADR-0029 D3). */}
                <td
                  data-testid="portfolio-realized-pnl-cell"
                  className={cn("px-3 py-2 text-right font-mono", PORTFOLIO_PANEL_PNL_TEXT)}
                >
                  {pos.realizedPnl}
                </td>
                <td className={cn("px-3 py-2 text-right font-mono", PORTFOLIO_PANEL_CELL_TEXT)}>
                  {pos.marketValue ?? naValue}
                </td>
                {/* 평가손익 셀 — PORTFOLIO_PANEL_PNL_TEXT 단일 톤. 양수/음수 색 분기 0 (ADR-0029 D3). */}
                <td
                  data-testid="portfolio-unrealized-pnl-cell"
                  className={cn("px-3 py-2 text-right font-mono", PORTFOLIO_PANEL_PNL_TEXT)}
                >
                  {pos.unrealizedPnl ?? naValue}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// =============================================================================
// PortfolioPanel (메인)
// =============================================================================

/**
 * Portfolio 패널 — 거래내역 + 포지션 (USER_PRIVATE, ADR-0029).
 *
 * 인증 미보유(401) 시 로그인 유도 표시(CustomScreenPanel 패턴).
 */
export function PortfolioPanel(): JSX.Element {
  const t = useTranslations("portfolio");
  const qc = useQueryClient();
  const asOf = useAsOfStore((s) => s.asOf);

  const [activeTab, setActiveTab] = useState<ActiveTab>("transactions");
  const [addError, setAddError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // WAI-ARIA tabs 키보드 네비 — tab 버튼 ref(포커스 이동용) + 화살표/Home/End
  // 핸들러. 자동 활성화 패턴(selection follows focus): 화살표로 이동 시 즉시
  // 해당 탭을 활성화하고 포커스를 옮긴다. roving tabindex 와 짝(선택 탭만 0).
  const tabRefs = useRef<Record<ActiveTab, HTMLButtonElement | null>>({
    transactions: null,
    positions: null,
  });

  function handleTabKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
    const current = TAB_ORDER.indexOf(activeTab);
    // noUncheckedIndexedAccess — TAB_ORDER[i] 는 ActiveTab | undefined. modulo
    // 산술이 항상 in-bounds 지만 TS 는 모르므로 undefined 허용 타입 + 가드.
    let next: ActiveTab | undefined;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      next = TAB_ORDER[(current + 1) % TAB_ORDER.length];
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      next = TAB_ORDER[(current - 1 + TAB_ORDER.length) % TAB_ORDER.length];
    } else if (event.key === "Home") {
      next = TAB_ORDER[0];
    } else if (event.key === "End") {
      next = TAB_ORDER[TAB_ORDER.length - 1];
    }
    if (next !== undefined) {
      event.preventDefault();
      setActiveTab(next);
      tabRefs.current[next]?.focus();
    }
  }

  // ── 거래내역 query ──────────────────────────────────────────────────────────

  const txQuery = useQuery<ReadonlyArray<PortfolioTransaction>, Error>({
    queryKey: ["portfolio", "transactions"],
    queryFn: ({ signal }) => listTransactions(signal),
    retry: false,
  });

  // ── 포지션 query ────────────────────────────────────────────────────────────

  const posQuery = useQuery<ReadonlyArray<PortfolioPosition>, Error>({
    queryKey: ["portfolio", "positions", asOf],
    queryFn: ({ signal }) => listPositions(asOf, signal),
    enabled: activeTab === "positions",
    retry: false,
  });

  // ── 거래 추가 mutation ──────────────────────────────────────────────────────

  const addTxMutation = useMutation<PortfolioTransaction, Error, AddTransactionParams>({
    mutationFn: (params) => addTransaction(params),
    onSuccess: () => {
      setAddError(null);
      void qc.invalidateQueries({ queryKey: ["portfolio", "transactions"] });
      // 포지션도 stale 처리.
      void qc.invalidateQueries({ queryKey: ["portfolio", "positions"] });
    },
    onError: (err) => {
      setAddError(t("addTransaction.errorPrefix", { message: err.message }));
    },
  });

  // ── 거래 삭제 mutation ──────────────────────────────────────────────────────

  const deleteTxMutation = useMutation<void, Error, string>({
    mutationFn: (txId) => deleteTransaction(txId),
    onSuccess: () => {
      setDeleteError(null);
      void qc.invalidateQueries({ queryKey: ["portfolio", "transactions"] });
      void qc.invalidateQueries({ queryKey: ["portfolio", "positions"] });
    },
    onError: (err) => {
      setDeleteError(t("transactions.deleteError", { message: err.message }));
    },
  });

  // ── 401 여부 판단 ───────────────────────────────────────────────────────────

  const txIs401 = txQuery.isError && isUnauthorized(txQuery.error);
  const posIs401 = posQuery.isError && isUnauthorized(posQuery.error);
  const addIs401 = addTxMutation.isError && isUnauthorized(addTxMutation.error);

  // ── 삭제 핸들러 ─────────────────────────────────────────────────────────────

  function handleDelete(txId: string): void {
    if (!window.confirm(t("transactions.deleteConfirm"))) return;
    deleteTxMutation.mutate(txId);
  }

  // ── 렌더 ───────────────────────────────────────────────────────────────────

  return (
    <div
      data-testid="portfolio-panel"
      className="space-y-4"
    >
      {/* 탭 네비게이션 — WAI-ARIA tabs (role=tablist/tab + roving tabindex +
          화살표 키 네비). 기존 <nav>+<button> 은 시맨틱·키보드 미준수였음. */}
      <div
        role="tablist"
        aria-label={t("tabsAriaLabel")}
        className="flex border-b border-neutral-200"
        onKeyDown={handleTabKeyDown}
      >
        {TAB_ORDER.map((tab) => {
          const selected = activeTab === tab;
          return (
            <button
              key={tab}
              ref={(el) => {
                tabRefs.current[tab] = el;
              }}
              type="button"
              role="tab"
              id={`portfolio-tab-${tab}`}
              aria-selected={selected}
              aria-controls={`portfolio-panel-${tab}`}
              // roving tabindex — 선택 탭만 Tab 키 진입점(0), 나머지는 -1.
              tabIndex={selected ? 0 : -1}
              onClick={() => setActiveTab(tab)}
              className={cn(
                "px-4 py-2 text-sm font-medium",
                selected
                  ? "border-b-2 border-neutral-900 text-neutral-900"
                  : "text-neutral-500 hover:text-neutral-700",
              )}
            >
              {t(TAB_LABEL_KEY[tab])}
            </button>
          );
        })}
      </div>

      {/* ── 거래내역 탭 ─────────────────────────────────────────────────────── */}
      {activeTab === "transactions" ? (
        <div
          role="tabpanel"
          id="portfolio-panel-transactions"
          aria-labelledby="portfolio-tab-transactions"
          tabIndex={0}
          className="space-y-4"
        >
          {/* 401 미인증 — 로그인 유도 */}
          {txIs401 || addIs401 ? (
            <div className="rounded-md border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm text-neutral-700">
              <p>{t("authError")}</p>
              <button
                type="button"
                onClick={() => void signIn("google")}
                className="mt-2 rounded-md border border-neutral-300 bg-white px-3 py-1 text-xs text-neutral-700 hover:border-neutral-400"
              >
                {t("signInButton")}
              </button>
            </div>
          ) : null}

          {/* 거래 입력 폼 */}
          <TransactionForm
            onSubmit={(params) => addTxMutation.mutate(params)}
            isPending={addTxMutation.isPending}
            t={t}
          />

          {/* 입력 오류 (비-401) */}
          {addTxMutation.isError && !addIs401 ? (
            <p role="alert" className="text-xs text-neutral-700">
              {addError}
            </p>
          ) : null}

          {/* 로딩 */}
          {txQuery.isLoading ? (
            <div className="space-y-2">
              <div className="h-8 animate-pulse rounded bg-neutral-100" />
              <div className="h-8 animate-pulse rounded bg-neutral-100" />
            </div>
          ) : null}

          {/* 거래내역 목록 로드 오류 (비-401) */}
          {txQuery.isError && !txIs401 ? (
            <p role="alert" className="text-xs text-neutral-700">
              {t("transactions.loadError", { message: txQuery.error.message })}
            </p>
          ) : null}

          {/* 삭제 오류 */}
          {deleteError !== null ? (
            <p role="alert" className="text-xs text-neutral-700">
              {deleteError}
            </p>
          ) : null}

          {/* 거래내역 목록 */}
          {txQuery.data !== undefined && !txIs401 ? (
            <TransactionList
              transactions={txQuery.data}
              onDelete={handleDelete}
              isDeleting={deleteTxMutation.isPending}
              t={t}
            />
          ) : null}
        </div>
      ) : null}

      {/* ── 포지션 탭 ────────────────────────────────────────────────────────── */}
      {activeTab === "positions" ? (
        <div
          role="tabpanel"
          id="portfolio-panel-positions"
          aria-labelledby="portfolio-tab-positions"
          tabIndex={0}
          className="space-y-4"
        >
          {/* 401 미인증 */}
          {posIs401 ? (
            <div className="rounded-md border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm text-neutral-700">
              <p>{t("authError")}</p>
              <button
                type="button"
                onClick={() => void signIn("google")}
                className="mt-2 rounded-md border border-neutral-300 bg-white px-3 py-1 text-xs text-neutral-700 hover:border-neutral-400"
              >
                {t("signInButton")}
              </button>
            </div>
          ) : null}

          {/* 로딩 */}
          {posQuery.isLoading ? (
            <div className="space-y-2">
              <div className="h-8 animate-pulse rounded bg-neutral-100" />
              <div className="h-8 animate-pulse rounded bg-neutral-100" />
              <div className="h-8 animate-pulse rounded bg-neutral-100" />
            </div>
          ) : null}

          {/* 오류 (비-401) */}
          {posQuery.isError && !posIs401 ? (
            <p role="alert" className="text-xs text-neutral-700">
              {t("positions.loadError", { message: posQuery.error.message })}
            </p>
          ) : null}

          {/* 포지션 표 */}
          {posQuery.data !== undefined && !posIs401 ? (
            <PositionTable
              positions={posQuery.data}
              asOf={asOf}
              t={t}
            />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

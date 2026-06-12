/**
 * PreTaxDisclosure — 세전 사실 고지 배너 (ADR-0035 D7 / M7 #6).
 *
 * 표시 조건:
 *   - `price-return:total-annual` 이 MetricCard 그리드에 표시되면 1회 렌더.
 *   - `dividend-yield:trailing-annual` 은 배당수익률(명목 지표)로 양도세와
 *     무관 — 세전 disclosure 트리거 대상이 아님(ADR-0035 D7 정확 범위).
 *   - 세전 factor 가 없으면 null.
 *
 * 8 기둥 §2.2 No Advice:
 *   - 경고(warning)가 아닌 중립 사실 고지. 판단·우열 어휘 0.
 *   - 색 상수: grayscale/neutral 계열만 (FORBIDDEN_SEMANTIC_COLOR_TOKENS 0).
 *   - PRE_TAX_DISCLOSURE_* 상수로 export — visual gate 검증 대상.
 *
 * i18n:
 *   - 메시지 키: "stock.metricsPreTaxDisclosure".
 *   - 한국어: "세전 기준 — 배당소득세·양도세 미반영, 실제 수익과 상이".
 */

import { useTranslations } from "next-intl";

// =============================================================================
// 중립 톤 상수 — visual gate 검증 대상 (ADR-0035 D7 / §2.2 No Advice)
//
// 규칙: grayscale/neutral/white 만. 판단색 토큰(red/green/amber/blue 등) 금지.
// pretax-disclosure-gate.test.tsx 가 이 상수 전체를 neutral 검증한다.
// =============================================================================

/** 배너 외곽 컨테이너 — 중립 테두리·배경. */
export const PRE_TAX_DISCLOSURE_CONTAINER =
  "mt-2 rounded border border-neutral-200 bg-neutral-50 px-3 py-2";

/** 배너 텍스트 색. */
export const PRE_TAX_DISCLOSURE_TEXT = "text-xs text-neutral-600";

// =============================================================================
// 세전 factor canonical_id 목록 — 본 고지 표시 기준.
// =============================================================================

/**
 * 세전 성격 factor — 이 중 하나라도 present 이면 고지 렌더.
 *
 * ADR-0035 D7: 세전 total-return 은 `price-return:total-annual` 만.
 * `dividend-yield:trailing-annual` 은 배당수익률(명목 지표)로 양도세와
 * 무관하므로 제외.
 */
export const PRE_TAX_FACTOR_IDS: ReadonlySet<string> = new Set([
  "price-return:total-annual",
]);

// =============================================================================
// Props
// =============================================================================

interface PreTaxDisclosureProps {
  /** 현재 표시 중인 factor 의 canonical_id 목록. */
  readonly factorIds: ReadonlyArray<string>;
}

// =============================================================================
// PreTaxDisclosure
// =============================================================================

/**
 * 세전 사실 고지 배너.
 *
 * `factorIds` 에 세전 factor 가 하나라도 포함되면 고지 문구를 1회 렌더.
 * 없으면 null — MetricCard 그리드 바깥에 렌더하므로 중복 없음.
 */
export function PreTaxDisclosure({
  factorIds,
}: PreTaxDisclosureProps): JSX.Element | null {
  const t = useTranslations("stock");

  const hasPreTaxFactor = factorIds.some((id) => PRE_TAX_FACTOR_IDS.has(id));
  if (!hasPreTaxFactor) {
    return null;
  }

  return (
    <p className={PRE_TAX_DISCLOSURE_CONTAINER}>
      <span className={PRE_TAX_DISCLOSURE_TEXT}>
        {t("metricsPreTaxDisclosure")}
      </span>
    </p>
  );
}

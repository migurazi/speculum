/**
 * PreTaxDisclosure 시각요소 게이트 — ADR-0035 D7 (M7 #6).
 *
 * disclosure-panel-gate.test.tsx 패턴으로:
 *   1. PreTaxDisclosure 가 export 하는 톤 상수가 모두 neutral/white — 등락색·판단색 토큰 부재.
 *   2. 세전 factor 포함 시 고지 문구 렌더. 미포함 시 null.
 *   3. 고지 문구에 추천/판단/우열 금지 어휘 없음.
 *   4. PRE_TAX_FACTOR_IDS 에 올바른 factor 포함.
 *   5. source 추론 — price-return → KRX, dividend-yield → FSC.
 */

import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  PreTaxDisclosure,
  PRE_TAX_DISCLOSURE_CONTAINER,
  PRE_TAX_DISCLOSURE_TEXT,
  PRE_TAX_FACTOR_IDS,
} from "@/components/StockDetail/PreTaxDisclosure";
import { inferFactorSource } from "@/lib/factor/source";
import { renderWithIntl } from "@/test-utils/intl";

// =============================================================================
// 상수 목록 — visual gate 검증 대상
// =============================================================================

/** PreTaxDisclosure 에서 export 하는 모든 톤 상수. */
const PRE_TAX_TONE_CONSTANTS: ReadonlyArray<string> = [
  PRE_TAX_DISCLOSURE_CONTAINER,
  PRE_TAX_DISCLOSURE_TEXT,
];

/**
 * 등락/판단 의미 색 토큰 — 톤 상수에 등장 금지.
 * disclosure-panel-gate.test.tsx 와 동일 목록.
 */
const FORBIDDEN_SEMANTIC_COLOR_TOKENS: ReadonlyArray<string> = [
  "red",
  "rose",
  "blue",
  "sky",
  "indigo",
  "green",
  "emerald",
  "teal",
  "lime",
  "amber",
  "orange",
  "yellow",
  "purple",
  "violet",
  "fuchsia",
  "pink",
  "cyan",
];

/**
 * No Advice 판단·추천·순위 어휘 — 시스템 생성 텍스트에 등장 금지.
 */
const FORBIDDEN_ADVISORY_WORDS: ReadonlyArray<string> = [
  "중요",
  "주목",
  "호재",
  "매수",
  "추천",
  "인기",
  "순위",
  "랭킹",
  "Top",
  "상위",
  "우수",
  "최고",
  "좋음",
  "높음",
];

// =============================================================================
// 게이트 1: 톤 상수 grayscale 검증
// =============================================================================

describe("PreTaxDisclosure 톤 상수 게이트 (ADR-0035 D7)", () => {
  it("모든 톤 상수는 grayscale(neutral/white) 계열이다", () => {
    for (const tone of PRE_TAX_TONE_CONSTANTS) {
      expect(tone, `톤 상수 '${tone}' 가 neutral/white 가 아님`).toMatch(
        /neutral|white/,
      );
    }
  });

  it("톤 상수에 등락/판단 의미색 토큰이 없다", () => {
    for (const tone of PRE_TAX_TONE_CONSTANTS) {
      for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
        expect(
          tone,
          `톤 상수 '${tone}' 에 금지 색 토큰 '${token}' 가 있음`,
        ).not.toMatch(new RegExp(`(?:^|[-\\s])${token}(?:[-\\s]|$)`));
      }
    }
  });
});

// =============================================================================
// 게이트 2: PRE_TAX_FACTOR_IDS 내용 검증
// =============================================================================

describe("PreTaxDisclosure PRE_TAX_FACTOR_IDS 게이트", () => {
  it("price-return:total-annual 이 포함된다", () => {
    expect(PRE_TAX_FACTOR_IDS.has("price-return:total-annual")).toBe(true);
  });

  it("dividend-yield:trailing-annual 은 포함되지 않는다 (ADR-0035 D7 — 양도세 무관)", () => {
    // dividend-yield 는 배당수익률(명목 지표)로 양도세와 무관 → 세전 disclosure 제외.
    expect(PRE_TAX_FACTOR_IDS.has("dividend-yield:trailing-annual")).toBe(false);
  });

  it("세전 무관 factor 는 포함되지 않는다", () => {
    expect(PRE_TAX_FACTOR_IDS.has("per:ttm-consolidated-ifrs")).toBe(false);
    expect(PRE_TAX_FACTOR_IDS.has("market-cap:ex-treasury")).toBe(false);
    expect(PRE_TAX_FACTOR_IDS.has("eps:basic-ttm-consolidated-ifrs")).toBe(false);
  });
});

// =============================================================================
// 게이트 3: 렌더 조건 — 세전 factor 유무
// =============================================================================

describe("PreTaxDisclosure 렌더 게이트 — 세전 factor 유무", () => {
  it("price-return:total-annual 포함 시 고지 렌더", () => {
    renderWithIntl(
      <PreTaxDisclosure factorIds={["market-cap:ex-treasury", "price-return:total-annual"]} />,
    );
    expect(
      screen.getByText(/세전 기준/),
    ).toBeInTheDocument();
  });

  it("dividend-yield:trailing-annual 단독 포함 시 고지 미렌더 (ADR-0035 D7 — 양도세 무관)", () => {
    // dividend-yield 는 배당수익률(명목 지표) — 양도세 disclosure 트리거 아님.
    renderWithIntl(
      <PreTaxDisclosure factorIds={["per:ttm-consolidated-ifrs", "dividend-yield:trailing-annual"]} />,
    );
    expect(screen.queryByText(/세전 기준/)).toBeNull();
  });

  it("price-return + dividend-yield 둘 다 포함 시 price-return 에 의해 고지 1회 렌더", () => {
    renderWithIntl(
      <PreTaxDisclosure
        factorIds={[
          "price-return:total-annual",
          "dividend-yield:trailing-annual",
        ]}
      />,
    );
    const disclosures = screen.getAllByText(/세전 기준/);
    expect(disclosures).toHaveLength(1);
  });

  it("세전 factor 미포함 시 고지 미렌더", () => {
    renderWithIntl(
      <PreTaxDisclosure
        factorIds={[
          "market-cap:ex-treasury",
          "per:ttm-consolidated-ifrs",
          "pbr:consolidated-ifrs",
        ]}
      />,
    );
    expect(screen.queryByText(/세전 기준/)).toBeNull();
  });

  it("빈 factorIds 시 고지 미렌더", () => {
    renderWithIntl(<PreTaxDisclosure factorIds={[]} />);
    expect(screen.queryByText(/세전 기준/)).toBeNull();
  });
});

// =============================================================================
// 게이트 4: 고지 문구 — 금지 어휘 0
// =============================================================================

describe("PreTaxDisclosure 고지 문구 게이트 — 금지 어휘 0", () => {
  it("고지 문구에 추천/판단/우열 어휘가 없다", () => {
    renderWithIntl(
      <PreTaxDisclosure factorIds={["price-return:total-annual"]} />,
    );
    const text = screen.getByText(/세전 기준/).textContent ?? "";
    for (const word of FORBIDDEN_ADVISORY_WORDS) {
      expect(
        text,
        `고지 문구에 금지 어휘 '${word}' 가 있음`,
      ).not.toContain(word);
    }
  });
});

// =============================================================================
// 게이트 5: source 추론 — price-return/dividend-yield
// =============================================================================

describe("inferFactorSource 게이트 — M7 #6 복합 출처 §2.8", () => {
  it("price-return:total-annual → [KRX, FSC] 복합 출처 (가격 KRX + 배당재투자 FSC)", () => {
    // §2.8 Conformance: 복합 출처 표기 의무.
    expect(inferFactorSource("price-return:total-annual")).toEqual(["KRX", "FSC"]);
  });

  it("dividend-yield:trailing-annual → [KRX, FSC] 복합 출처 (배당 FSC / 가격 KRX)", () => {
    // §2.8 Conformance: dividend-yield = 배당(FSC) / 가격(KRX) 복합.
    expect(inferFactorSource("dividend-yield:trailing-annual")).toEqual(["KRX", "FSC"]);
  });

  it("volume-turnover:avg-20d → KRX (단일)", () => {
    expect(inferFactorSource("volume-turnover:avg-20d")).toBe("KRX");
  });

  it("기존 KRX namespace 회귀 — market-cap → KRX (단일)", () => {
    expect(inferFactorSource("market-cap:ex-treasury")).toBe("KRX");
  });

  it("기존 DART namespace 회귀 — per → DART (단일)", () => {
    expect(inferFactorSource("per:ttm-consolidated-ifrs")).toBe("DART");
  });

  it("기존 DART namespace 회귀 — eps → DART (단일)", () => {
    expect(inferFactorSource("eps:basic-ttm-consolidated-ifrs")).toBe("DART");
  });
});

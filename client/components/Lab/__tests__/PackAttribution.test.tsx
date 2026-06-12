/**
 * PackAttribution — 출처·라이선스·provenance 표시 테스트 (ADR-0006 D5 / ADR-0032 #6).
 *
 * 외부 저자 pack 사용 시 license + citation(publisher) + source_url 을 use-시점에
 * 노출하는지, 표시할 게 없으면 렌더하지 않는지, 위험 scheme provenance 는 링크하지
 * 않는지(XSS 차단) 검증한다.
 *
 * v2 identity 추가 (ADR-0034 D1/D4/D7):
 *   - v2 slug(@{publisher}/{slug}) 면 인증 publisher + disclosure 렌더.
 *   - v1 slug(user/·community/) 면 v2 섹션 미렌더.
 *   - grayscale 톤(판단색 0).
 */

import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { FactorPack } from "@/lib/api/factor-packs";
import { renderWithIntl } from "@/test-utils/intl";

import { PackAttribution } from "../PackAttribution";

const _BASE: FactorPack = {
  pack_slug: "community/x",
  version: "1.0.0",
  factors: [],
  citation: { title: "외부 저자 pack" },
};

const _BASE_V2: FactorPack = {
  pack_slug: "@my-handle/x",
  version: "1.0.0",
  factors: [],
  citation: { title: "v2 pack" },
};

/** 금지 의미색 토큰 — backtest-visual-gate.test.tsx FORBIDDEN_SEMANTIC_COLOR_TOKENS 와 동일. */
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

describe("PackAttribution (ADR-0006 D5 / ADR-0032 #6)", () => {
  it("license+publisher+source_url 이 모두 있으면 셋 다 표시한다", () => {
    const pack = {
      ..._BASE,
      license: "MIT",
      citation: { title: "외부 저자 pack", publisher: "blogger-kim" },
    } as FactorPack;
    renderWithIntl(
      <PackAttribution
        pack={pack}
        sourceUrl="https://example.com/packs/x.json"
      />,
    );
    expect(screen.getByTestId("pack-attribution-license").textContent).toContain(
      "MIT",
    );
    expect(
      screen.getByTestId("pack-attribution-publisher").textContent,
    ).toContain("blogger-kim");
    const link = screen
      .getByTestId("pack-attribution-provenance")
      .querySelector("a");
    expect(link?.getAttribute("href")).toBe("https://example.com/packs/x.json");
    expect(link?.getAttribute("rel")).toBe("noopener noreferrer");
    expect(link?.getAttribute("target")).toBe("_blank");
  });

  it("표시할 출처·라이선스 정보가 없으면 아무것도 렌더하지 않는다", () => {
    renderWithIntl(<PackAttribution pack={_BASE} sourceUrl={null} />);
    expect(screen.queryByTestId("pack-attribution")).toBeNull();
  });

  it("위험 scheme source_url 은 provenance 링크를 렌더하지 않는다", () => {
    renderWithIntl(
      <PackAttribution pack={_BASE} sourceUrl="javascript:alert(1)" />,
    );
    // license/publisher 도 없으므로 블록 전체 미렌더.
    expect(screen.queryByTestId("pack-attribution-provenance")).toBeNull();
    expect(screen.queryByTestId("pack-attribution")).toBeNull();
  });

  it("license 만 있어도 블록을 표시한다(부분 정보)", () => {
    const pack = { ..._BASE, license: "CC-BY-4.0" } as FactorPack;
    renderWithIntl(<PackAttribution pack={pack} sourceUrl={null} />);
    expect(screen.getByTestId("pack-attribution")).toBeTruthy();
    expect(screen.getByTestId("pack-attribution-license").textContent).toContain(
      "CC-BY-4.0",
    );
    expect(screen.queryByTestId("pack-attribution-publisher")).toBeNull();
  });
});

describe("PackAttribution v2 identity (ADR-0034 D1/D4/D7)", () => {
  it("v2 slug(@{publisher}/{slug}) 면 인증 publisher 를 표시한다", () => {
    renderWithIntl(<PackAttribution pack={_BASE_V2} sourceUrl={null} />);
    const verifiedEl = screen.getByTestId("pack-attribution-verified-publisher");
    expect(verifiedEl.textContent).toContain("@my-handle");
  });

  it("v2 slug 면 disclosure 텍스트를 렌더한다 (D7 권위 오인 방지)", () => {
    renderWithIntl(<PackAttribution pack={_BASE_V2} sourceUrl={null} />);
    const disclosureEl = screen.getByTestId("pack-attribution-disclosure");
    // disclosure 텍스트에 OAuth/handle 관련 중립 고지 포함 확인.
    expect(disclosureEl.textContent).toContain("OAuth");
    // 실제 회사 등 권위 보증 아님 표현 포함.
    expect(disclosureEl.textContent).toContain("보증하지 않습니다");
  });

  it("v2 slug 면 attribution 블록이 렌더된다(다른 필드 없어도)", () => {
    renderWithIntl(<PackAttribution pack={_BASE_V2} sourceUrl={null} />);
    expect(screen.getByTestId("pack-attribution")).toBeTruthy();
    expect(screen.getByTestId("pack-attribution-v2-identity")).toBeTruthy();
  });

  it("v1 slug(user/) 면 v2 identity 섹션을 렌더하지 않는다", () => {
    const v1Pack: FactorPack = { ..._BASE, pack_slug: "user/my-pack" };
    renderWithIntl(<PackAttribution pack={v1Pack} sourceUrl={null} />);
    // 다른 attribution 필드도 없으니 블록 전체 null.
    expect(screen.queryByTestId("pack-attribution-v2-identity")).toBeNull();
    expect(screen.queryByTestId("pack-attribution")).toBeNull();
  });

  it("v1 slug(community/) 면 v2 identity 섹션을 렌더하지 않는다", () => {
    renderWithIntl(<PackAttribution pack={_BASE} sourceUrl={null} />);
    expect(screen.queryByTestId("pack-attribution-v2-identity")).toBeNull();
  });

  it("v2 identity 엘리먼트 className 에 판단 의미색이 없다", () => {
    renderWithIntl(<PackAttribution pack={_BASE_V2} sourceUrl={null} />);

    const identityEl = screen.getByTestId("pack-attribution-v2-identity");
    const verifiedEl = screen.getByTestId("pack-attribution-verified-publisher");
    const disclosureEl = screen.getByTestId("pack-attribution-disclosure");

    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(identityEl.className, `v2-identity 에 금지 색 '${token}'`).not.toMatch(
        new RegExp(`-${token}-`),
      );
      expect(verifiedEl.className, `verified-publisher 에 금지 색 '${token}'`).not.toMatch(
        new RegExp(`-${token}-`),
      );
      expect(disclosureEl.className, `disclosure 에 금지 색 '${token}'`).not.toMatch(
        new RegExp(`-${token}-`),
      );
    }
  });

  it("v2 identity 표시 텍스트에 권위 신호·순위·인기 어휘가 없다", () => {
    renderWithIntl(<PackAttribution pack={_BASE_V2} sourceUrl={null} />);
    const identityEl = screen.getByTestId("pack-attribution-v2-identity");
    const text = identityEl.textContent ?? "";

    const FORBIDDEN_ADVISORY_WORDS = ["추천", "인기", "1위", "랭킹", "Top", "우수", "최고"];
    for (const word of FORBIDDEN_ADVISORY_WORDS) {
      expect(text, `v2 identity 에 금지 어휘 '${word}'`).not.toContain(word);
    }
  });

  it("v2 slug 에서 publisher handle 을 정확히 파싱한다 (@ 포함 / 다중 슬래시)", () => {
    const pack: FactorPack = {
      ..._BASE_V2,
      pack_slug: "@acme-corp/value-pack",
    };
    renderWithIntl(<PackAttribution pack={pack} sourceUrl={null} />);
    const verifiedEl = screen.getByTestId("pack-attribution-verified-publisher");
    expect(verifiedEl.textContent).toContain("@acme-corp");
    // slug 의 value-pack 부분이 publisher 로 오인되지 않아야 함.
    expect(verifiedEl.textContent).not.toContain("value-pack");
  });
});

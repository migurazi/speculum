"use client";

/**
 * PackAttribution — 출처·라이선스·provenance 표시(ADR-0006 D5 / ADR-0032 #6).
 *
 * 외부 저자 pack 을 editor 에서 사용할 때 라이선스·출처를 use-시점에 노출한다
 * (제3자 콘텐츠 귀속 의무 — ADR-0006 D5). 표시 항목:
 *   - license: editor 핵심 타입(FactorPack) 밖 필드라 런타임 봉인 body 에서
 *     방어적으로 읽는다(import 된 pack 은 봉인 body 에 license 보유).
 *   - publisher: citation.publisher(외부 저자 기입).
 *   - source_url: import provenance 사이드카(https 만 링크 — 위험 scheme 클릭 차단).
 * 셋 다 없으면(직접 작성 신규 pack) 아무것도 렌더하지 않는다. 톤은 중립
 * (큐레이션/순위/판단 신호 0 — §2.2 No Advice / ADR-0028 D3 일관).
 *
 * v2 identity (ADR-0034 D1/D4/D7):
 *   - pack_slug 가 @{publisher}/{slug} 형태면 v2 slug 로 판정.
 *   - "인증된 publisher(이 인스턴스): @{publisher}" 를 neutral 톤으로 표시.
 *   - disclosure(D7): publisher 는 OAuth 계정 기반 handle 이며 외부 권위를
 *     보증하지 않는다는 중립 고지 — 권위 오인 방지. 경고가 아닌 사실 고지.
 *   - v1 slug(user/·community/)면 기존 표시 유지.
 */

import { useTranslations } from "next-intl";

import type { FactorPack } from "@/lib/api/factor-packs";

interface PackAttributionProps {
  readonly pack: FactorPack;
  /**
   * ADR-0032 D3 import provenance(출처 URL). URL import 로 들어온 pack 만 값이
   * 있으며 content_hash 와 무관한 row 메타다. null=출처 없음(직접 작성).
   */
  readonly sourceUrl: string | null;
}

/**
 * v2 slug 파싱 — `@{publisher}/{slug}` 형태 여부 확인.
 *
 * 조건: `@` 로 시작 + 이후에 `/` 포함.
 * 반환: publisher handle 문자열(@ 제외), v1/비해당이면 null.
 */
function parseV2Publisher(packSlug: string): string | null {
  if (!packSlug.startsWith("@")) return null;
  const withoutAt = packSlug.slice(1);
  const slashIndex = withoutAt.indexOf("/");
  if (slashIndex < 1) return null;
  return withoutAt.slice(0, slashIndex);
}

export function PackAttribution({
  pack,
  sourceUrl,
}: PackAttributionProps): JSX.Element | null {
  const t = useTranslations("lab");

  // v2 slug(@{publisher}/{slug}) 판정.
  const v2Publisher = parseV2Publisher(pack.pack_slug);

  // license 는 FactorPack 타입에 없으나 import 된 봉인 body 에 존재 가능 — 방어적 read.
  const license = (pack as FactorPack & { license?: unknown }).license;
  const licenseText = typeof license === "string" ? license : null;
  const publisher = pack.citation.publisher ?? null;
  // provenance 링크는 https 만(저장단 https-only 강제와 일관 — 표시단 방어).
  const provenanceUrl =
    sourceUrl !== null && /^https:\/\//i.test(sourceUrl) ? sourceUrl : null;

  // v2 identity 표시: v2Publisher 있으면 섹션 렌더(다른 필드 없어도).
  const hasV2Identity = v2Publisher !== null;
  const hasLegacyContent =
    licenseText !== null || publisher !== null || provenanceUrl !== null;

  if (!hasV2Identity && !hasLegacyContent) {
    return null;
  }

  return (
    <div
      data-testid="pack-attribution"
      className="space-y-0.5 border-t border-neutral-200 pt-2 text-[11px] text-neutral-500"
    >
      <p className="font-medium text-neutral-600">
        {t("pack.attributionHeading")}
      </p>

      {/* v2 identity — ADR-0034 D1/D4/D7 */}
      {hasV2Identity && (
        <div
          data-testid="pack-attribution-v2-identity"
          className="space-y-0.5"
        >
          <p
            data-testid="pack-attribution-verified-publisher"
            className="text-neutral-600"
          >
            {t("identity.verifiedPublisher", { publisher: v2Publisher })}
          </p>
          {/* D7 disclosure — 권위 오인 방지 중립 고지. */}
          <p
            data-testid="pack-attribution-disclosure"
            className="text-neutral-400"
          >
            {t("identity.disclosure")}
          </p>
        </div>
      )}

      {/* 기존 v1 attribution 필드 — license / citation.publisher / provenance */}
      {licenseText !== null && (
        <p data-testid="pack-attribution-license">
          {t("pack.licenseLabel")}: {licenseText}
        </p>
      )}
      {publisher !== null && (
        <p data-testid="pack-attribution-publisher">
          {t("pack.publisherLabel")}: {publisher}
        </p>
      )}
      {provenanceUrl !== null && (
        <p data-testid="pack-attribution-provenance">
          {t("pack.provenanceLabel")}:{" "}
          <a
            href={provenanceUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="font-mono text-neutral-600 underline decoration-neutral-300 hover:text-neutral-800"
          >
            {provenanceUrl}
          </a>
        </p>
      )}
    </div>
  );
}

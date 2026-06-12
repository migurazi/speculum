/**
 * Factor canonical_id → SourceLabel 추론 — 단일 진실 출처.
 *
 * MetricCard / CompareGrid 양쪽이 동일 heuristic 을 적용해야 같은 종목·factor
 * 가 페이지에 따라 다른 source 로 표시되는 drift 회피. 본 파일 단일 export.
 *
 * 운영 시 backend `FactorValueOut` 에 explicit `source` 필드가 추가되면 본
 * heuristic 제거 + backend 값 직접 사용 (oracle T38 L1).
 *
 * 패턴 — `<namespace>:<variant>` canonical_id 의 namespace 로 분기:
 *   - `market-cap`, `price`, `volume-turnover` → KRX (가격·시장 데이터)
 *   - `price-return` → [KRX, FSC] 복합 출처
 *     (가격 데이터: KRX, 배당재투자 데이터: FSC — ADR-0035 D3 §2.8)
 *   - `dividend-yield` → [KRX, FSC] 복합 출처
 *     (배당수익률 = 배당(FSC) / 가격(KRX) — ADR-0035 D3 §2.8)
 *   - 그 외 (`per`, `pbr`, `roe`, `eps`, ...) → DART (재무제표)
 *
 * §2.8 Conformance: 복합 출처 factor 는 두 출처를 모두 표기해 출처 누락 0.
 */

import type { SourceLabel } from "@/lib/types/source";

/** 가격·시장 데이터 단일 출처 — KRX. */
const KRX_NAMESPACES: ReadonlySet<string> = new Set([
  "market-cap",
  "price",
  "volume-turnover",
]);

/**
 * 복합 출처 factor namespace — KRX(가격) + FSC(배당) 양쪽 데이터 사용.
 *
 * - `price-return`: 총수익률 = 가격(KRX) + 배당재투자(FSC).
 * - `dividend-yield`: 배당수익률 = 배당(FSC) / 가격(KRX).
 */
const KRX_FSC_NAMESPACES: ReadonlySet<string> = new Set([
  "price-return",
  "dividend-yield",
]);

/** 복합 출처 상수 — 중복 할당 방지. */
const KRX_FSC_COMPOUND: readonly [SourceLabel, SourceLabel] = ["KRX", "FSC"];

/**
 * Factor canonical_id 에서 데이터 출처를 추론한다.
 *
 * @returns 단일 출처 factor → `SourceLabel`,
 *          복합 출처 factor (price-return / dividend-yield) → `readonly SourceLabel[]`
 */
export function inferFactorSource(
  canonicalId: string,
): SourceLabel | ReadonlyArray<SourceLabel> {
  const prefix = canonicalId.split(":")[0] ?? "";
  if (KRX_NAMESPACES.has(prefix)) {
    return "KRX";
  }
  if (KRX_FSC_NAMESPACES.has(prefix)) {
    return KRX_FSC_COMPOUND;
  }
  return "DART";
}

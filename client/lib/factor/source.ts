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
 *   - `market-cap`, `price` → KRX (시장 데이터)
 *   - 그 외 (`per`, `pbr`, `roe`, `eps`, ...) → DART (재무제표)
 */

import type { SourceLabel } from "@/lib/types/source";

const KRX_NAMESPACES: ReadonlySet<string> = new Set(["market-cap", "price"]);

export function inferFactorSource(canonicalId: string): SourceLabel {
  const prefix = canonicalId.split(":")[0] ?? "";
  if (KRX_NAMESPACES.has(prefix)) {
    return "KRX";
  }
  return "DART";
}

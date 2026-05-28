/**
 * MetricCard — Stock Detail 의 단일 지표 카드.
 *
 * 8 기둥 §2.1 Fidelity 의 implementation — 모든 값에 SourceAttribution
 * (식·출처·기준일) 동반. backend `FactorValue` 의 canonical_id / name / unit /
 * value / is_na / na_reason 모두 표시.
 *
 * source 결정:
 *   - factor canonical_id 의 namespace 로부터 추론. M0 = 모든 빌트인 factor
 *     가 DART (재무제표) 기반이라 default "DART". KRX 시가총액 factor 는
 *     "market-cap" prefix → "KRX". 별도 cycle 에서 backend 가 explicit source
 *     필드 노출 (Fidelity 보강).
 *   - 본 M0 의 source heuristic 은 단순화 — 운영 시 backend wire schema
 *     확장이 정도.
 *
 * 관련 ADR:
 * - ADR-0007 D2 (Source attribution 의무).
 * - ADR-0004 (PER/PBR/ROE 산출 정의).
 * - M0_PLAN T37 / AC-F-04.
 */

import {
  SourceAttribution,
  type SourceAttributionProps,
} from "@/components/SourceAttribution";
import type { FactorValue } from "@/lib/api/stocks";
import { inferFactorSource } from "@/lib/factor/source";
import { cn } from "@/lib/utils";

interface MetricCardProps {
  readonly factor: FactorValue;
  readonly asOf: string;
  readonly className?: string;
}

/**
 * factor canonical_id 의 단순 산출식 표시 — M0 minimum.
 *
 * 운영 시 backend 가 factor pack 의 formula AST 를 wire 로 노출하면 그 값을
 * 우선 사용. 본 함수는 fallback — canonical_id 그대로 표시.
 */
function describeFormula(factor: FactorValue): string {
  // backend FactorValue 에 formula text 가 없어 canonical_id 자체를 식으로 표시.
  // T22 의 factor pack 본격 활용 시 backend wire 가 formula 추가.
  return factor.canonical_id;
}

export function MetricCard({
  factor,
  asOf,
  className,
}: MetricCardProps): JSX.Element {
  const source = inferFactorSource(factor.canonical_id);
  const formula = describeFormula(factor);

  if (factor.is_na || factor.value === null) {
    return (
      <div
        className={cn(
          "rounded-lg border border-neutral-200 bg-white p-4",
          className,
        )}
      >
        <div className="text-xs font-medium text-neutral-500">
          {factor.name}
        </div>
        <div className="mt-2 text-base text-neutral-400">N/A</div>
        {factor.na_reason !== null ? (
          <div className="mt-1 text-xs text-neutral-500">
            {factor.na_reason}
          </div>
        ) : null}
        <div className="mt-2 font-mono text-[10px] text-neutral-400">
          {factor.canonical_id}
        </div>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "rounded-lg border border-neutral-200 bg-white p-4",
        className,
      )}
    >
      <div className="text-xs font-medium text-neutral-500">{factor.name}</div>
      <div className="mt-2">
        <SourceAttribution
          value={
            <span className="text-base">
              {factor.value}
              {factor.unit !== "ratio" && factor.unit !== "" ? (
                <span className="ml-1 text-xs text-neutral-500">
                  {factor.unit}
                </span>
              ) : null}
            </span>
          }
          source={source}
          formula={formula}
          asOf={asOf}
        />
      </div>
      <div className="mt-2 font-mono text-[10px] text-neutral-400">
        {factor.canonical_id}
      </div>
    </div>
  );
}

/** Exported for tests / future override of SourceAttribution props subset. */
export type { SourceAttributionProps };

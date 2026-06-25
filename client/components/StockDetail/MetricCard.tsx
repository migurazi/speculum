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
 * percent unit 처리 (ADR-0035 D7):
 *   - backend 가 ratio(소수) 를 반환. 표시 layer 에서만 ×100 변환.
 *   - `unit === "percent"` 일 때 `parseFloat(value) * 100` 후 소수 2자리 표기.
 *   - 이중 ×100 금지 — server 는 ratio 그대로 반환, client 만 변환.
 *
 * 관련 ADR:
 * - ADR-0007 D2 (Source attribution 의무).
 * - ADR-0004 (PER/PBR/ROE 산출 정의).
 * - ADR-0035 D7 (총수익률/배당수익률 percent 표시).
 * - M0_PLAN T37 / AC-F-04.
 */

import { useTranslations } from "next-intl";

import { SourceAttribution } from "@/components/SourceAttribution";
import type { FactorValue } from "@/lib/api/stocks";
import { formatPercentValue } from "@/lib/factor/format";
import { parseNaReason } from "@/lib/factor/na-reason";
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
  // N/A 사유의 한국어 변환용 — naReason 네임스페이스로 스코프.
  const tReason = useTranslations("stock.naReason");

  /**
   * backend 기계 코드 na_reason → 사람이 읽는 한국어. field 라벨은 i18n
   * `fields.<key>` 에서 찾되, 미등록 field 는 raw key 로 graceful fallback
   * (새 factor 추가 시 라벨 누락이 crash 가 아니라 식별자 노출로 degrade).
   * 알 수 없는 형식(unknown)은 원문 그대로(정보 손실 0).
   */
  const naReasonText = (raw: string): string => {
    const r = parseNaReason(raw);
    if (r.kind === "unknown") return r.raw;
    const fieldKey = `fields.${r.field}`;
    const field = tReason.has(fieldKey) ? tReason(fieldKey) : r.field;
    if (r.kind === "missing_input") {
      return tReason("missingInput", { field });
    }
    return tReason("insufficientSeries", {
      field,
      requested: r.requested ?? "?",
      got: r.got ?? "?",
    });
  };

  // 계약: is_na=false 이면 value!==null (backend FactorValueOut 보장). 그러나
  // 계약 위반 데이터(is_na=false && value===null)에도 crash 하지 않도록 이
  // 가드가 두 경우(is_na || value===null)를 동일하게 N/A 로 흡수한다.
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
            {naReasonText(factor.na_reason)}
          </div>
        ) : null}
        <div className="mt-2 font-mono text-[10px] text-neutral-400">
          {factor.canonical_id}
        </div>
      </div>
    );
  }

  // 위 가드를 통과한 시점에 factor.value 는 string(계약상 non-null). 그러나
  // non-null assertion(!) 대신 명시 fallback 으로 방어 — 계약 위반 데이터가
  // 가드를 우회해 도달하더라도 crash 없이 빈 문자열로 표시.
  const value = factor.value ?? "";

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
              {factor.unit === "percent"
                ? formatPercentValue(value)
                : value}
              {factor.unit !== "ratio" &&
              factor.unit !== "percent" &&
              factor.unit !== "" ? (
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

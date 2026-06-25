/**
 * N/A 사유 코드 파서 — backend `factor_evaluator.py` 의 기계용 na_reason 을
 * 구조 분해한다(표시 layer 의 한국어 변환은 MetricCard 가 i18n 으로 수행).
 *
 * backend 가 생성하는 형식(factor_evaluator.py):
 *   - `missing_input:<field>`                         (입력 데이터 결손, 일상 N/A)
 *   - `insufficient_series:<field>:requested=N,got=M` (분기 시계열 부족)
 *
 * 본 모듈은 **순수 파서**(i18n 무관) — `<field>` 키만 떼어내고, MetricCard 가
 * `naReason.fields.<field>` i18n 키로 한국어 라벨을 입힌다. 미지의 형식은
 * `unknown` 으로 흘려보내 호출부가 raw 표시로 graceful fallback.
 */

export type NaReason =
  | { readonly kind: "missing_input"; readonly field: string }
  | {
      readonly kind: "insufficient_series";
      readonly field: string;
      readonly requested: number | null;
      readonly got: number | null;
    }
  | { readonly kind: "unknown"; readonly raw: string };

const MISSING_PREFIX = "missing_input:";
const INSUFFICIENT_PREFIX = "insufficient_series:";

/**
 * na_reason 문자열 → 구조 분해. 알 수 없는 형식은 `unknown`(raw 보존).
 *
 * `insufficient_series:<field>:requested=N,got=M` 에서 field 자체에 콜론이 없으므로
 * 첫 콜론을 기준으로 field 와 tail 을 분리한다. requested/got 은 정규식으로 추출하되,
 * 형식 변형(미래 backend 변경)에 대비해 누락 시 null 로 둔다.
 */
export function parseNaReason(raw: string): NaReason {
  if (raw.startsWith(MISSING_PREFIX)) {
    return { kind: "missing_input", field: raw.slice(MISSING_PREFIX.length) };
  }
  if (raw.startsWith(INSUFFICIENT_PREFIX)) {
    const rest = raw.slice(INSUFFICIENT_PREFIX.length);
    const colon = rest.indexOf(":");
    const field = colon >= 0 ? rest.slice(0, colon) : rest;
    const tail = colon >= 0 ? rest.slice(colon + 1) : "";
    const reqMatch = /requested=(\d+)/.exec(tail);
    const gotMatch = /got=(\d+)/.exec(tail);
    return {
      kind: "insufficient_series",
      field,
      requested: reqMatch ? Number(reqMatch[1]) : null,
      got: gotMatch ? Number(gotMatch[1]) : null,
    };
  }
  return { kind: "unknown", raw };
}

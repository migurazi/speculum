/**
 * Factor 표시용 포맷 헬퍼 — 단일 진실 출처.
 *
 * MetricCard · CompareGrid · Market Overview 등 factor 값을 표시하는 모든
 * component 가 동일 로직을 사용하도록 공통 추출. 이중 ×100 방지.
 *
 * 원칙 (ADR-0035 D7):
 *   - backend 는 percent factor 를 ratio(소수, 예: 0.05) 로 반환.
 *   - 표시 layer 에서만 ×100 변환. server 는 변환하지 않음.
 *   - 이중 ×100 금지 — 본 함수는 ratio→percent 1회 변환만 책임.
 */

/**
 * percent unit 값을 표시용 문자열로 변환.
 *
 * backend 가 ratio(소수, 예: 0.05) 를 반환하므로 표시 시 ×100 후 소수 2자리.
 * 이중 ×100 방지 — server 는 ratio 그대로 반환, client 표시 layer 만 변환.
 *
 * 예: "0.05" → "5.00%"
 * 예: "0.1234" → "12.34%"
 *
 * @param raw backend raw ratio string
 * @returns 표시용 문자열 (유효하지 않은 숫자면 raw 그대로)
 */
export function formatPercentValue(raw: string): string {
  const num = parseFloat(raw);
  if (!isFinite(num)) return raw;
  return `${(num * 100).toFixed(2)}%`;
}

/**
 * 통계값(string | null) 을 percent unit 기준으로 표시용 문자열로 변환.
 *
 * market overview 집계값(mean/median/p25/p75/min/max) 표시 시 사용.
 * null 이면 "—" 반환.
 *
 * @param val backend 통계 string | null
 * @param isPercent true 이면 ×100 변환 + "%" 부착
 */
export function formatStatValue(val: string | null, isPercent: boolean): string {
  if (val === null) return "—";
  if (isPercent) return formatPercentValue(val);
  return val;
}

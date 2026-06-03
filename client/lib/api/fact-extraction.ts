/**
 * AI 공시 사실추출 API client — POST /api/stocks/{code}/disclosures/extract-facts.
 *
 * ADR-0031 D1: 자유 서술 요약 아닌 구조화 사실 필드 추출만.
 *   스키마 = 공시유형·금액·일자·당사자·수량 슬롯.
 *   자유 서술·해석·압축 문장 필드 없음 — transport 타입에서 구조적 강제.
 *
 * ADR-0031 D3: 출처 강제 + AI 디스클레이머 게이트.
 *   모든 응답에 source(rcept_no + dart_url) + disclaimerRequired 포함.
 *   disclaimerRequired 미충족 시 UI 계층이 결과 차단(게이트 위임).
 *
 * ADR-0031 D4: trigger = 사용자 명시 1종목·1공시 on-demand.
 *   자동 추출·feed·일괄 처리 경로 없음 — 호출 책임은 호출자.
 *
 * ADR-0031 D5: 미래 전망/평가/추천 필드 없음 — 스키마에서 구조적 차단.
 *
 * 오류 구분:
 *   - 503: LLM 운영 미연동(정상 상태). ApiError.status === 503.
 *   - 422: 출력 게이트 fail-closed(금지어 추출 차단). ApiError.status === 422.
 *   - 그 외: fetchJson 에서 ApiError 로 전파.
 *
 * snake↔camel 변환 패턴: disclosures.ts / backtest.ts 와 동일.
 *
 * 관련:
 *   - ADR-0031 — AI 공시 사실추출 설계.
 *   - lib/api/client.ts — fetchJson (Authorization Bearer 자동 주입).
 *   - components/StockDetail/DisclosureFactsPanel.tsx — 표시 계층.
 */

import { fetchJson } from "./client";

// =============================================================================
// Wire 타입 (backend snake_case)
// =============================================================================

/**
 * POST /api/stocks/{code}/disclosures/extract-facts 요청 body.
 */
interface ExtractFactsBodyWire {
  readonly rcept_no: string;
  readonly disclosure_title: string;
}

/**
 * 구조화 사실 필드 wire — ADR-0031 D1/D5.
 *
 * 슬롯: 공시유형·금액·일자·당사자·수량만.
 * 자유 서술·전망·평가·추천 필드 없음.
 */
interface DisclosureFactsWire {
  readonly disclosure_type: string;
  readonly amounts: ReadonlyArray<string>;
  readonly dates: ReadonlyArray<string>;
  readonly parties: ReadonlyArray<string>;
  readonly quantities: ReadonlyArray<string>;
}

/**
 * 출처 wire — ADR-0031 D3 출처 강제.
 */
interface FactSourceWire {
  readonly rcept_no: string;
  readonly dart_url: string;
}

/**
 * POST /api/stocks/{code}/disclosures/extract-facts 응답 wire.
 */
interface ExtractFactsResultWire {
  readonly facts: DisclosureFactsWire;
  readonly source: FactSourceWire;
  /** 항상 true — ADR-0031 D3: 디스클레이머 게이트 필요 여부. */
  readonly disclaimer_required: boolean;
}

// =============================================================================
// 공개 도메인 타입 (camelCase)
// =============================================================================

/**
 * 구조화 사실 필드 — ADR-0031 D1/D5.
 *
 * 사실 슬롯: 공시유형·금액·일자·당사자·수량.
 * 전망/요약/추천/평가 필드 의도적으로 부재.
 */
export interface DisclosureFacts {
  /** 공시 유형 (예: "유상증자결정", "자기주식취득결정"). */
  readonly disclosureType: string;
  /** 금액 목록 (예: ["100억원", "1,000,000,000원"]). */
  readonly amounts: ReadonlyArray<string>;
  /** 일자 목록 (예: ["2026-07-01", "2026-08-15"]). */
  readonly dates: ReadonlyArray<string>;
  /** 당사자 목록 (예: ["삼성전자", "주주총회"]). */
  readonly parties: ReadonlyArray<string>;
  /** 수량 목록 (예: ["1,000,000주"]). */
  readonly quantities: ReadonlyArray<string>;
}

/**
 * 출처 — ADR-0031 D3 출처 강제.
 *
 * DART 원문 링크는 UI 계층에서 항상 표시해야 한다.
 */
export interface FactSource {
  /** DART 공시 접수번호. */
  readonly rceptNo: string;
  /** DART 원문 viewer URL. */
  readonly dartUrl: string;
}

/**
 * extractDisclosureFacts 반환 타입 — ADR-0031 D1/D3.
 *
 * 포함: facts(구조화 사실) + source(출처) + disclaimerRequired(AI 디스클레이머 게이트).
 * 미포함: 전망/요약/추천/평가 필드 일절 없음 (D5).
 */
export interface ExtractFactsResult {
  readonly facts: DisclosureFacts;
  readonly source: FactSource;
  /**
   * AI 디스클레이머 게이트 필요 여부 — ADR-0031 D3.
   * true 이면 UI 계층은 반드시 디스클레이머와 함께 결과를 표시해야 한다.
   * false 이면 결과 단독 표시 금지 — 현재 backend 는 항상 true 반환.
   */
  readonly disclaimerRequired: boolean;
}

/**
 * extractDisclosureFacts 입력 파라미터.
 */
export interface ExtractFactsParams {
  /** DART 공시 접수번호 — 1공시 on-demand (ADR-0031 D4). */
  readonly rceptNo: string;
  /** 공시 제목 — LLM context 보조. */
  readonly disclosureTitle: string;
}

// =============================================================================
// snake↔camel 변환
// =============================================================================

/** wire facts → camelCase DisclosureFacts. */
function mapFacts(w: DisclosureFactsWire): DisclosureFacts {
  return {
    disclosureType: w.disclosure_type,
    amounts: w.amounts,
    dates: w.dates,
    parties: w.parties,
    quantities: w.quantities,
  };
}

/** wire source → camelCase FactSource. */
function mapSource(w: FactSourceWire): FactSource {
  return {
    rceptNo: w.rcept_no,
    dartUrl: w.dart_url,
  };
}

/** wire 응답 → ExtractFactsResult (camelCase). */
function mapResult(w: ExtractFactsResultWire): ExtractFactsResult {
  return {
    facts: mapFacts(w.facts),
    source: mapSource(w.source),
    disclaimerRequired: w.disclaimer_required,
  };
}

// =============================================================================
// 공개 함수
// =============================================================================

/**
 * AI 공시 사실추출 — POST /api/stocks/{code}/disclosures/extract-facts.
 *
 * ADR-0031 D4: 사용자가 명시 요청한 1종목·1공시에 한해 on-demand 호출.
 * ADR-0031 D1: 반환 타입 ExtractFactsResult — 구조화 사실 슬롯만. 자유 서술 없음.
 * ADR-0031 D3: source + disclaimerRequired 항상 포함 — UI 계층이 게이트 적용.
 *
 * @param code   KRX 6자리 종목코드.
 * @param params rceptNo + disclosureTitle.
 * @param signal AbortController signal.
 * @returns ExtractFactsResult (camelCase, 구조화 사실+출처+디스클레이머플래그).
 * @throws ApiError(503) LLM 운영 미연동 — 정상 비활성화 상태. UI 에서 중립 안내.
 * @throws ApiError(422) 출력 게이트 fail-closed — 금지어 추출 차단. UI 에서 중립 안내.
 * @throws ApiError       그 외 네트워크/4xx/5xx 오류.
 */
export async function extractDisclosureFacts(
  code: string,
  params: ExtractFactsParams,
  signal?: AbortSignal,
): Promise<ExtractFactsResult> {
  const body: ExtractFactsBodyWire = {
    rcept_no: params.rceptNo,
    disclosure_title: params.disclosureTitle,
  };

  const wire = await fetchJson<ExtractFactsResultWire>(
    `/api/stocks/${encodeURIComponent(code)}/disclosures/extract-facts`,
    {
      method: "POST",
      body,
      signal,
    },
  );

  return mapResult(wire);
}

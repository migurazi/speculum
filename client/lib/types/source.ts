/**
 * Source attribution types — ADR-0002 D3 의 7-tuple Citation 의 frontend
 * 사용자 시각 표현. backend `app.models.source_citation::SourceKind` 와
 * 1:1 매핑.
 *
 * 운영 시 backend API 의 `source` 필드 string value 가 본 union 의 멤버.
 * Mismatch 시 TypeScript 빌드 에러 — backend / frontend wire format 일관성
 * 강제.
 */

/**
 * SourceLabel — 데이터 출처 식별. backend `SourceKind` enum value 와 동일.
 *
 * | 코드     | 의미                                        |
 * |---------|---------------------------------------------|
 * | DART    | 금융감독원 전자공시시스템 (재무제표·공시)        |
 * | KRX     | 한국거래소 (가격·시가총액·종목 마스터)             |
 * | PYKRX   | pykrx 라이브러리 (KRX 공식 wrapper)            |
 * | FDR     | FinanceDataReader (가격 cross-check)        |
 * | ECOS    | 한국은행 경제통계시스템 (M1+ 거시지표)            |
 * | KOSIS   | 통계청 (M1+ 산업·경제 통계)                    |
 * | USER_INPUT | 사용자 manual 입력 (M2+ Factor Lab)        |
 */
export type SourceLabel =
  | "DART"
  | "KRX"
  | "PYKRX"
  | "FDR"
  | "ECOS"
  | "KOSIS"
  | "USER_INPUT";

/**
 * 한글 표시명 — Tooltip / footer 의 사용자 visible 라벨.
 *
 * ADR-0006 D5 의 데이터 라이선스 표시 의무 — "출처: ..." 형태.
 */
// oracle 리뷰 M3 — `satisfies` 로 object literal 완전성 검사 명시. SourceLabel
// union 확장 시 누락된 키가 컴파일 에러 (runtime undefined render 차단).
export const SOURCE_LABEL_KO = {
  DART: "금융감독원 전자공시시스템(DART)",
  KRX: "한국거래소(KRX)",
  PYKRX: "한국거래소(KRX) / pykrx",
  FDR: "FinanceDataReader",
  ECOS: "한국은행 경제통계시스템(ECOS)",
  KOSIS: "통계청 KOSIS",
  USER_INPUT: "사용자 입력",
} as const satisfies Record<SourceLabel, string>;

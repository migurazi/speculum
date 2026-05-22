# M{N} Conformance Review (YYYY-MM-DD)

> 사용자 메모리 [[tessera-milestone-conformance-review]] 의 동형 정책 — 매 마일스톤 squash 직전 Momus 의 전방위 표준 검토. Speculum 의 기둥 8 (Conformance to Standards) 의 process implementation.

## 검토 범위

본 마일스톤 산출물 + 다음 마일스톤 작업지시서 + 핵심 문서 (CONCEPT / ARCHITECTURE / ROADMAP / M{N}_PLAN) 의 전방위 검토.

## 검토 기준

| 기준 | 출처 | 우선순위 |
|---|---|---|
| **KRX 시장 운영 규정** | 한국거래소 규정집 | H |
| **K-IFRS 회계기준** | 금융감독원 회계기준원 | H |
| **DART 공시 양식 / XBRL 표준** | DART 시스템 매뉴얼 | H |
| **자본시장법 + 시행령** (특히 유사투자자문업) | 법제처 국가법령정보센터 | H |
| **개인정보보호법** | 개인정보보호위원회 | H |
| **KSIC 한국표준산업분류** | 통계청 | M |
| **한국은행 ECOS 데이터 정의** | ECOS 매뉴얼 | M (M1+) |
| **10 기둥 (CONCEPT §2)** | `docs/CONCEPT.md` | H |
| **자매 프로젝트 ADR 일관성** | Tessera/Norma ADR | M |

## 분류 요약

| 등급 | 개수 | 처리 상태 |
|---|---|---|
| **Critical** | _ | _/_ |
| **High** | _ | _/_ |
| **Medium** | _ | _/_ |
| **Low** | _ | _/_ |
| **합** | _ | _/_ |

- **Critical** — squash 차단. 표준 정면 위반 또는 법적 위험.
- **High** — squash 가능하나 다음 마일스톤 진입 전 처리.
- **Medium** — 추후 마일스톤.
- **Low** — trivial.

## 항목 형식

각 발견 항목은 다음 형식:

```
### V{n}. <한 줄 제목> ({등급})

**위치**: <파일 경로:line> 또는 <문서 §>
**표준**: <어느 표준의 어느 조항>

문제:
- (구체)

권장:
- (구체)
- **ADR-NNNN 신설** (있으면)
```

## 진행

1. Momus (Opus, read-only) 위임으로 라운드 1 검토 → 발견 리스트
2. 사용자와 우선순위 합의
3. Critical/High 처리 (코드 + ADR + 문서)
4. 라운드 2 재검토
5. (필요 시) 라운드 3
6. squash 가능 수준 도달 → 마일스톤 종료

## 결과 archive

본 work-order 의 자체 + 라운드별 momus-rev{n}.md (있다면) + 최종 squash 가능 판정 메모.

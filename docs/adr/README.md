# Speculum ADR 목록

> Architecture Decision Records — Speculum 의 모든 큰 결정의 영구 기록.
> 자매 프로젝트 Tessera 의 ADR 문화 차용.

## 형식

- 파일명: `adr-NNNN-{slug}.md`
- 템플릿: [`_template.md`](_template.md)
- 섹션: Context / Decision / Rationale / Consequences / Alternatives Considered / References
- Status: PROPOSED / ACCEPTED / SUPERSEDED / DEPRECATED

## 목록

### Phase 0 (M0 진입 전 결정 — 2026-05-22 작성)

| ADR | 제목 | Status | 의존 |
|---|---|---|---|
| [0001](adr-0001-price-adjustment.md) | 가격 보정 정책 (수정주가 정의) | ACCEPTED | - |
| [0002](adr-0002-factor-fact-model.md) | Factor / Fact 3-layer 데이터 모델 | ACCEPTED | - |
| [0003](adr-0003-data-source-adapter.md) | Data Source Adapter 패턴 | ACCEPTED | 0001, 0002 |
| [0004](adr-0004-market-cap-eps-per.md) | 시가총액·EPS·PER 산출 정의 | ACCEPTED | 0002 |
| [0005](adr-0005-ifrs-consolidated.md) | K-IFRS 연결 vs 별도 기본 선택 | ACCEPTED | 0002, 0004 |
| [0006](adr-0006-legal-review.md) | 법률 검토 결과 정리 (M0 무료 운영) | ACCEPTED (조건부) | - |
| [0007](adr-0007-default-ui-rules.md) | 디폴트 UI 규약 (No Advice 강제) | ACCEPTED | 0006 |
| [0008](adr-0008-as-of-date.md) | As-of Date Picker 일급 UI element | ACCEPTED | 0002, 0009 |
| [0009](adr-0009-corporate-action.md) | Corporate Action 분류 + 보정 정책 | ACCEPTED | 0001, 0002 |
| 0010 | 홈 화면 정체성 | ACCEPTED | 0006, 0007 |
| [0011](adr-0011-watchlist-scope.md) | Watchlist Scope (M0 알림 미구현) | ACCEPTED | 0006, 0007, 0008, 0009, 0010 |
| 0012 | Conformance Review Work-Order 템플릿 | ACCEPTED (별도 위치 — `docs/work-orders/_template-conformance-review.md`) | - |

**0010 파일명**: `adr-0010-home-screen.md`

### Phase 1+ (예정)

| ADR | 제목 | 시점 |
|---|---|---|
| 0013 | ETF / 우선주 / 리츠 v0.2 분리의 통계적 한계 | M0 release 전 |
| 0014 | Screen Run snapshot DB schema 본격 | M0~M1 경계 |
| 0015 | 한국 시장 휴장일·반장 정밀 처리 | M0 T17 작성 |
| 0016 | DART 양식 회사별 override mapper | M0 T16 작성 |
| 0017 | 충돌 감지 threshold + alert 정책 | M0 T18 작성 |
| 0018 | KRX 정보데이터시스템 라이선스 답변 반영 | M0 release 전 |
| 0019 | 변호사 자문 결과 반영 (ADR-0006 superseded) | M0 release 전 |

## 자매 프로젝트와의 결

- **Tessera ADR** — DICOM 표준 준수의 implementation. PS3.x 의 각 조항 별 ADR. Speculum 은 자본시장법·KRX·DART·K-IFRS 의 각 영역 별 ADR 동형.
- **Norma ADR** — Web-first 단일 프로젝트 ADR (전체 통합). Speculum 은 client/server 분리이므로 Tessera 스타일이 더 적합.
- **Momus conformance review** — 매 마일스톤 squash 직전 의무 (사용자 메모리 [[tessera-milestone-conformance-review]]). Speculum 도 동형 (`docs/work-orders/m0-conformance-review.md`).

## ADR 작성 절차

1. 의존 ADR 확인 (위 표)
2. `_template.md` 복사 → `adr-NNNN-{slug}.md`
3. Status = PROPOSED 로 시작
4. 결정 항목 정리 — 사용자 결정 필요한 곳은 `AskUserQuestion`
5. Status = ACCEPTED
6. 본 README 의 목록에 추가
7. Related 섹션의 다른 ADR / 문서 link 갱신

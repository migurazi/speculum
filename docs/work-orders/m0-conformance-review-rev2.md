# M0 Conformance Review — Momus 라운드 2 보고서

| | |
|---|---|
| **검토자** | Momus (Opus 4.7 [1M], read-only) |
| **일자** | 2026-05-28 |
| **상위 work-order** | [m0-conformance-review.md](m0-conformance-review.md) |
| **라운드 1** | [m0-conformance-review-rev1.md](m0-conformance-review-rev1.md) |
| **결론** | **OKAY — Squash 가능** |

---

## §1. 라운드 1 fix 검증 결과

| 항목 | 등급 | 본 cycle 처리 범위 | 판정 |
|---|---|---|---|
| **V1** DART effective_date 보수 정책 | Critical | 단기 (ADR-0012 D1 신고기한 lag) | **PASS** (잔존 docstring drift — Low, 본 cycle 정정) |
| **V2** ConsentModal v2 + 페이지 신설 | Critical | 단기 + 중기 통합 | **PASS** (잔존 CI 페이지 존재 검사 — Medium, M1) |
| **V3** PriceRecord.trading_value | High | 단기 schema + batch (FieldProvider M1) | **PASS** (intended M1) |
| **V4** `진입` 단독 제거 | High | 완전 (ADR-0013 신설) | **PASS** |
| **V5** ADR-0007 본문 SoT 일치 | High | 완전 | **PASS** |
| **V15** allowed_phrases + playwright 제외 | (cycle 신규) | 완전 | **PASS** |

### V1 PASS 근거

- ADR-0012 신설 (D1~D7) + `_disclosure_deadline` (`dart_adapter.py:524`) + estimated_fields marker 보존.
- test 35 통과 (`test_disclosure_deadline_matrix` 5 parametrize + estimated_fields + `test_batch_savepoint_rollback` as_of 갱신).
- 자본시장법 제160조 (Q1~Q3 +45d, Q4 +90d) 정확. 윤년 edge case test cover.
- silent look-ahead bias 0 — 신고기한 = 100% 공시 완료.
- **잔존 (Low)**: ADR-0012 D7 명시한 `pit_protocols.py:101` + `db/orm/financials.py:55` docstring drift → **본 cycle 의 rev2 후속 commit 에서 정정**.

### V2 PASS 근거 (잔존 CI 가드 ✅ 본 cycle fix)

- ConsentModal v2 (3 별도 체크박스 + 4 절 ADR-0006 D7.1 본문 그대로).
- useConsent v2 storage key (v1 → v2 — 기존 사용자도 재동의 강제).
- 3 신규 페이지: `/privacy` (D6.2 9 절) + `/terms` (D7 9 조) + `/disclaimer` (D2 + D7.2).
- DisclaimerFooter placeholder → 실 link 교체.
- E2E 21 통과 (6 ConsentModal + 3 page sanity + 12 다른 spec).
- 개인정보보호법 제22조 별도 동의 + 제22조의2 만 14세 + 제30조 처리방침 항시 공개 implementation.
- **잔존 (Medium) — ✅ 본 cycle fix**: `tools/check_disclaimer_coverage.py`
  에 `/privacy`, `/terms`, `/disclaimer` 페이지 file 존재 검사 추가 + `--root`
  CLI 인자 (testability). CLI 단위 test 6 case 추가
  (`test_check_disclaimer_coverage_cli.py`). 페이지 삭제 시 CI fail —
  footer/consent link dead link 차단.

### V3 PASS 근거

- PriceRecord + PriceDailyORM + converters + KRX batch `_build_price_records` 의 `trading_value` 영구화.
- Alembic 0004 의 `server_default=0` (M0 dev 빈 DB + 운영 backfill 안전성).
- test fixture 3 파일 광범위 갱신 + 902 server pytest pass.
- **잔존 (intended M1)**: FieldProvider wiring + factor_evaluator dry-run test 는 M1 backlog 명시.

### V4/V5/V15 PASS 근거

- V4: ADR-0013 신설 (D9.2 절차 의무 4 항목 모두 충족) + SoT 의 `진입` 단독 제거.
- V5: ADR-0007 D4.1/D4.2 본문 = 카테고리별 대표 예시 + SoT 참조 명시.
- V15: allowed_phrases 5 추가 + `_DEFAULT_EXCLUDES` 에 playwright artifact.
- `tools/check_forbidden_words.py 0` 위반.
- 잔존: 없음.

---

## §2. 미검토 영역 신규 발견 (W1~W7)

라운드 1 미검토 영역에서 신규 Critical/High 0.

### W1. KIND URL 정확성 — citation `url` 가 dead link 가능 (Low) — ✅ fix 완료

**위치**: `server/app/adapters/pykrx_adapter.py:93-96` `_KRX_STOCK_DETAIL_URL_TEMPLATE`. 사용처: line 217 / 291 / 347.

**표준**: ADR-0002 D3 SourceCitation `url` 의 의미 — "사용자 click 시 1차 자료 도달".

**문제**:
- `https://kind.krx.co.kr/common/disclsviewer.do?method=search&isurCmpyCd={code}` 의 `isurCmpyCd` 는 KIND 발행회사 고유번호 (8자리) 이지 6자리 KRX 종목코드 아님.
- 6자리 그대로 매개 → KIND 404.

**권장**: M1 backlog. `data.krx.co.kr` 의 `mdiLoader` URL 또는 `url=None`. **squash blocker 아님**.

**Fix**: pykrx_adapter 의 `_KRX_STOCK_DETAIL_URL_TEMPLATE` 제거 + 3 호출 (`fetch_ohlcv_by_date_range` / `fetch_market_cap_by_date_range` / `fetch_stock_master`) 의 `url=None` 통일. FDR adapter 와 일관. test_pykrx_adapter:159 의 assertion 갱신.

### W2. `screen_runs / watchlists / screener_sets.user_id` FK 부재 (Low, intended)

**위치**: `server/alembic/versions/20260527_0001~20260528_0003` — `users` 테이블 미존재.

**문제**: M0 single-user SYSTEM_USER_ID 모드. T31 NextAuth 합류 시 users 테이블 + FK.

**권장**: work-order §1.2 의 intended 한계 list 에 명시 권장. **squash blocker 아님**.

### W3. `factor-pack-v1.json` exprConst `type: number` precision (Low, intended)

**위치**: `shared/schemas/factor-pack-v1.json:172-178`.

**문제**: JSON Schema `type: number` = IEEE 754 double. 2^53 초과 정수 precision loss. Builtin pack 안전 (작은 const). Community pack (M2+) risk.

**권장**: M2+ Factor Lab 시점에 schema 갱신 (`oneOf: [{type: number}, {type: string}]`). **squash blocker 아님**.

### W4. `financials.(code_lineage_id, effective_date)` 복합 인덱스 부재 (Low) — ✅ fix 완료

**위치**: `server/alembic/versions/20260527_0001_initial_schema.py:201-210`.

**문제**: prices_daily 는 lineage 인덱스 있음 (line 166-170), financials 는 (code, account, effective_date) 만. lineage 단위 historical scan hot path 부재.

**권장**: M1 backlog. M0 운영 규모 (~5000 row) 영향 미미. **squash blocker 아님**.

**Fix**: Alembic migration 0005 `ix_financials_lineage_date` 추가 + ORM `__table_args__` 에 Index 정의. prices_daily 와 일관성. server pytest 911/911 pass.

### W5. ConflictDetector / estimated_fields marker DB 영구화 부재 (Low, intended)

**위치**: `server/batch/dart_daily.py:320-355`, `server/app/db/orm/financials.py`.

**문제**: adapter 의 `estimated_fields = frozenset({"effective_date"})` 가 runtime 만 살아있음. ADR-0012 D3 가 "M1+ list.json fetch 시 자연 제거" 명시 — 의도된 trade-off. V1 fix 가 effective_date 자체를 보수적 산출하므로 silent look-ahead bias 0.

**권장**: ADR-0012 D6 의 M1+ work-order. **Low (intended)**.

### W6. ValueError handler 미등록 (Low, V12 잔존) — ✅ fix 완료

**위치**: `server/app/api/exception_handlers.py`.

**문제**: `assert_clean` 의 ValueError 가 검출 어휘 echo (server log). 사용자 visible echo 위험은 middleware 가 차단.

**권장**: M1 backlog. **squash blocker 아님**.

**Fix**: `ForbiddenWordsAssertError(ValueError)` sub-class 도입 +
`register_exception_handlers` 의 명시 handler 등록 (`status_code=500` +
generic body + server log `ERROR speculum.forbidden_words.assert`).
response body 의 어휘 echo 0 검증 (test_api/test_forbidden_words_handler.py
의 `test_handler_returns_500_without_word_echo`). backward compat — 기존
`except ValueError` catch 도 작동.

### W7. `_jcs.py` Decimal 입력 docstring 부재 (Low) — ✅ fix 완료

**위치**: `server/app/services/_jcs.py:44-65`.

**문제**: `json.dumps` 직접 호출 — Decimal 입력 시 TypeError. 현 호출자 모두 dict literal 이라 안전. Docstring 명시 권장.

**권장**: docstring 정정. **Low**.

**Fix**: `canonicalize_jcs` docstring 의 Args 에 "Decimal 입력 차단 — 호출자가 `str(decimal_value)` 변환 의무" 명시 + Raises 에 `TypeError` 추가.

---

## §3. 분류 요약

| 등급 | 본 라운드 발견 | 처리 상태 |
|---|---|---|
| **Critical** | **0** | — |
| **High** | **0** | — |
| **Medium** | 1 (V2 CI 가드) | M1 backlog |
| **Low** | 6 (V1 docstring + W1/W3/W4/W5/W6/W7) | M1 backlog (V1 docstring 은 본 cycle rev2 commit 에서 정정) |

라운드 1 → 라운드 2 변화:
- Critical 2/2 해결.
- High 4/4 해결.
- 미검토 영역 신규 Critical / High 0.

---

## §4. 결론 — Squash 가능 판정

**판정: OKAY (squash 가능)**

### 근거

1. **라운드 1 Critical 2 (V1 / V2) 가 단단히 해결**:
   - V1: ADR-0012 + `_disclosure_deadline` 보수 정책. silent look-ahead bias 0.
   - V2: ConsentModal v2 + 3 페이지 + footer link. 개인정보보호법 implementation 충족.

2. **라운드 1 High 4 (V3 / V4 / V5 / V6) 가 squash 가능 수준 처리**:
   - V3: schema + batch wiring 100%. FieldProvider M1.
   - V4: ADR-0013 + SoT 제거.
   - V5: ADR-0007 본문 SoT 참조 형식.
   - V6 (ADR-0018/0019/AC-L-02): release blocker 이지 squash blocker 아님 — 라운드 1 §6 명시.

3. **미검토 영역 신규 Critical/High 0**:
   - `screener_sets.py` / `runs.py` — CurrentUserDep IDOR 차단 + user_id 검증 OK.
   - Alembic 0001~0004 — 정합성 + dialect 분기 + append-only trigger OK.
   - `_jcs.py` / `factor_evaluator.py` / `snapshot_versions.py` / `as_of_policy.py` — 결정성 + N/A propagation + import topology 단단.
   - pykrx/fdr citation — W1 (KIND URL) 만 Low.
   - E2E — coverage 충실 + flakiness 낮음.
   - shared schemas — content_hash + prototype pollution 보호 안전.

### Release blocker (T47 전 의무, squash 와 분리)

- ADR-0018 (KRX 라이선스 답변) + ADR-0019 (변호사 자문) — 처리방침 / 이용약관 / 면책조항 1차 초안 본문의 변호사 검토 반영.
- AC-L-02 — 변호사 자문 완료.

### 라운드 2 잔존 항목 (M1 backlog, 비-squash blocker)

| 라벨 | 위치 | 등급 |
|---|---|---|
| V1 docstring drift | pit_protocols.py:101, db/orm/financials.py:55 | Low (본 rev2 commit 정정) |
| V2 CI 가드 페이지 검사 | tools/check_disclaimer_coverage.py | Medium |
| V12 / W6 ValueError handler | exception_handlers.py | Low |
| W1 KIND URL | pykrx_adapter.py:93 | Low |
| W2 users 테이블 부재 | Alembic | Low (intended) |
| W3 exprConst number | factor-pack-v1.json | Low (M2+) |
| W4 financials lineage 인덱스 | Alembic 0001 | Low |
| W5 estimated_fields DB 영구화 | dart_daily.py | Low (intended) |
| W7 _jcs.py Decimal docstring | _jcs.py | Low |

### 권고

본 라운드 2 검토는 **squash 통과**. develop branch 에서 T47 release 전 위 release blocker 처리. 본 라운드 2 의 잔존 M1 backlog 항목은 squash 후 별도 작은 cycle 로 처리 권장 — 본 cycle 의 squash 지연 회피.

**Squash 판정: OKAY.** M0 v0.1.0 의 "검경" + "정직한 거울" 정체성이 V1 fix 로 보장. ADR governance (ADR-0012 / ADR-0013 신설) 도 D9 절차 준수로 정합.

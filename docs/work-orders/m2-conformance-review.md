# M2 Conformance Review (T83)

| | |
|---|---|
| **Reviewer** | Momus (Opus 4.8 [1M], read-only) + 정합성 정정(claude) |
| **Date** | 2026-06-02 |
| **Scope** | M2 전 작업 (T69~T82) vs 8 기둥(CONCEPT §2.1~2.10) + Reproducibility freeze |
| **Reference** | `m0-conformance-review-rev2.md` 형식/엄격도 |
| **Verdict** | **OKAY — M2 종료(squash) 가능.** Critical 0 / High 0 / Medium 1(잔여) / Low 3 — 전부 M3+ backlog 또는 의도된 한계 |

> M2 의 "검경" 정체성 — 추천하지 않고 정직하게 비추는 거울 — 이 No Advice 연산자집합
> 차단 + 소표본 디스클로저 + 재현 freeze 로 일관 보장됨. ADR governance(0021~0024 신설,
> ADR-0007 D4.5/D5/D8.2 확장)도 정합.

---

## 1. 8 기둥별 판정

### §2.2 No Advice — **PASS (가장 강한 영역)**
- **연산자 집합 차단**: `shared/schemas/factor-pack-v1.json` op enum 에 `rank`/`top_n`/`bottom_n`/`sign`/`step` **부재**. 허용 = add/sub/mul/div/sum_last_n_quarters/avg/ratio_pct/weighted_sum/zscore/percentile/min_max_scale/winsorize. ADR-0022 D1 "코드베이스에 없는 게 안전" 을 schema(`additionalProperties:false`+enum)에 구현.
- **빌트인 0 게이트(T73)**: `factor_pack.py:354` `validate_builtin_no_composite` 가 `load_builtin_pack` 에서 정적 검사 → fail-loud. custom 경로만 composite 허용.
- **자산군 필터 No Advice(D4)**: `asset-class-no-advice-gate.test.tsx` 가 프로덕션 소스에서 `RecommendedETFs`/`TopReits`/`RecommendedPreferred` 등 20개 큐레이션 심볼 부재 강제. 기본 필터 = `("common",)`.
- **Notes 시스템 생성 0**: `test_notes_no_system_write.py` 가 AST 로 `Note`/`StockNoteORM`/`note_record_to_orm` 구성이 화이트리스트 5개 모듈 밖 부재 + migration INSERT 부재 강제.
- **출력 게이트(T74)**: `lab-visual-gate.test.tsx` — grayscale 톤, 등락색/색조컬러맵/랭킹배지 부재, 행순서=입력순서.

### §2.1 Fidelity — **PASS**
- Composite 산출식 always-visible(ADR-0022 D6): `FactorForm.tsx` `<FormulaPreview>` 가 가중치·정규화 모집단·연산식을 점수 옆에 항상 표시.
- 자산군 N/A vs 값(ADR-0023 D3): 우선주 발행사-귀속 N/A(차용 거부), 리츠 PER 값+디스클로저(D8), ETF 재무 자연 N/A — 정의 은폐 0.
- 소표본 디스클로저 값 숨김 0(ADR-0024): percentile n==1 에서도 100 보존, 값+모집단크기 동시 표시.

### §2.3 Active Inspection — **PASS**
- universe 편입 ≠ 추천(ADR-0023 D4): ETF/우선주/리츠 universe 에서 안 뺌(자연 N/A).
- 미인증 fact 탐색 허용(ADR-0021 D4): screen/fact 라우트 `CurrentUserDep` 부재.
- 자산군 필터 동등 가시성(보통주도 같은 체크박스). 기본=보통주의 비중립성을 ADR 이 정직하게 인정.

### §2.4 PIT — **PASS**
- 분포 모집단 as_of active(survivorship): `db_universe_distribution.py:286` `_codes_by_security_type_map`.
- 분포 입력 effective_date<=as_of. ECOS vintage<=as_of(T81). 정정 history look-ahead 0(T82, `fetch_history` as_of 필터).

### §2.5 Open Data Sufficiency — **PASS**
- 소표본 디스클로저(ADR-0024)가 "N=3 percentile 이 N=300 처럼 보임" 갭 차단. ETF NAV 출처=KRX 공식(1차 자료).

### §2.7 Observation — **PASS**
- percentile/zscore=사실, 라벨링 부재. 디스클로저 중립어("모집단 N개"·"소표본") — 가치어 부재 게이트.

### §2.10 Reproducibility + user 격리 — **PASS**
- **user_id hash 무관**: `screen_run.py` hash_input={query,as_of,result_codes,data_versions} — user_id 부재(마이그레이션 무해, ADR-0021 D5).
- **IDOR 폐쇄**: screen_runs append-only(save owner 무관 거부). watchlist/screener_sets/notes owner-check. **Notes `list_for_code` 가 user_id AND code_lineage_id 둘 다 WHERE**(`sql_notes_repository.py:84`) + negative test.
- **security_type hash 입력(D7)**: 같은 선택=byte 동일.
- **DISTRIBUTION_POLICY_VERSION freeze**: 1.0→1.1(partition) bump → data_versions → result_hash.
- **small_sample result_codes 격리(ADR-0024 D4c)**: `test_small_sample_result_codes_gate.py` AST 정적 검증.
- **identity hash uuid 제외(D8)**: `compute_factor_hash(exclude_key="uuid")` 결정성. canonical replace 금지.
- **backfill 안전(D6)**: frozen batch_cutoff=(started_at,id) 가 미래 backfill 배제. byte-동일 재현 테스트.

---

## 2. 자동 게이트 사각지대 (적대적 검토)

### V-M2-1 (정정: 현재 트리거 불가 → Low/문서한계) — screener universe-상대 조건
Momus 원 지적: `screen.py:255` `_passes_all_conditions` 가 `universe_distribution` 미주입 → screener 에 percentile/zscore factor 쓰면 silent 빈결과.
**정정(claude 검증)**: screen 은 `ActivePackDep`(빌트인 pack)만 사용하고 `_compile_conditions`(`screen.py:200-207`)가 **active pack 에 없는 factor 를 400 거부**한다. 빌트인 pack 은 composite/universe-상대 op 가 **0**(T73 게이트)이므로 universe-상대 factor 를 screen 조건에 넣는 것 자체가 불가능(400). 따라서 **현재 M2 에선 트리거 불가**. custom pack 을 screen 에 주입하는 경로(M3+)가 생기면 그때 (a)분포 provider 주입 또는 (b)422 명시 거부가 선결. **현 시점 코드 수정 불요 — intended-limit.**

### V-M2-2 (Medium) — off-universe code 의 min(n) 귀속 — **[수정완료 2026-06-02]**
`factor_packs.py:288` evaluate 시 `bind_target_security_type(code)` 가 as_of active 에 없는 code 를 default "common" 으로 귀속 → off-universe(폐지 직후 등) 우선주 미리보기가 common 모집단 percentile 로 표시되며 디스클로저가 모집단 불일치 미노출(§2.1 미묘).
**수정**: `db_universe_distribution.py` 에 `_OFF_UNIVERSE_TARGET` sentinel 도입 — off-universe code 는 어느 partition 과도 겹치지 않는 sentinel 로 귀속해 **빈 모집단 → universe-상대 op(percentile/zscore/min_max) 자연 N/A + sample_size 0**. 모집단 불일치 값 대신 정직한 N/A(§2.1 Fidelity). 종목-국소 factor 무영향. 회귀 가드 `test_partition_bind_off_universe_yields_empty_population`.

### V-M2-3 (Low, M3+) — XSS 게이트 문자열 휴리스틱
`markdown-xss-chokepoint-gate.test.tsx` 는 `dangerouslySetInnerHTML` 사용 파일이 `renderMarkdown` 문자열 포함 시 통과(data-flow 미추적). 실제 chokepoint(`markdown.ts`)는 DOMPurify allowlist 로 견고 — 게이트는 회귀 tripwire. 권고: ESLint `react/no-danger` + chokepoint allowlist 격상.

### V-M2-4 (Low, M1/M3 backlog) — security_type DB CHECK 부재
`20260601_0013_security_type.py` 는 String(16) NOT NULL server_default 만 — DB CHECK 없음. 허용값은 application(`validate_security_type`)만 강제. SQLite 호환 보류(m0 W 항목 동급).

### V-M2-5 (Low, intended) — code_lineage_id FK 부재
`20260602_0014_stock_notes.py` code_lineage_id FK 미설정(watchlist_items 선례, stocks_master 미bootstrap 호환). user 격리·재현 무관.

---

## 3. M1 회귀 — **PASS (회귀 0)**
- hash_input 불변(user_id 미포함) + security_types default ("common",) → M1 보통주 run byte 동일(구 export security_types=None→("common",) 하위호환).
- reproduce 가 frozen batch_cutoff 로 ETF/우선주 미래 fact 배제. `test_reproduction.py` 14 시나리오 가드.
- **backend 1528 passed / 4 skipped(integration smoke flakiness), client 418 passed** — 게이트·negative test 전부 존재.

---

## 4. 최종 판정 — **OKAY (squash 가능)**

Critical 0 / High 0. 8 기둥 + §2.10 freeze 의 핵심 불변식이 코드+enum+게이트 다층 강제.
잔여 사각지대(V-M2-2~5)는 Medium 이하 + M3+ backlog/intended. V-M2-1 은 현재 트리거 불가로 정정.

### M3+ backlog
| 라벨 | 위치 | 등급 |
|---|---|---|
| V-M2-1 screener universe-상대 조건(custom pack screen 도입 시) | `screen.py:255` | 문서한계(현재 트리거불가) |
| V-M2-2 off-universe code 의 min(n) 귀속 오도 | `db_universe_distribution.py` (`_OFF_UNIVERSE_TARGET`) | **수정완료** |
| V-M2-3 XSS 게이트 data-flow 격상 | `markdown-xss-chokepoint-gate.test.tsx` | Low |
| V-M2-4 security_type DB CHECK | `20260601_0013_security_type.py` | Low |
| V-M2-5 code_lineage_id FK | `20260602_0014_stock_notes.py` | Low(intended) |

### 미충족 AC (M2 종료 시 명시 deferred)
- **AC-M2-F-01** Google OAuth(T68) — 외부 credential blocker. M2.1/M3.
- **AC-M2-F-05** ETF/리츠 전용 factor(NAV/괴리율/AUM/FFO) — 데이터 어댑터(R4) 의존. 뷰·선택 UI·출처(ETF=KRX) 완료, 데이터 합류 deferred.
- **AC-M2-F-02** "저장" — custom pack DB 영속화 부재(stateless export/import 로 갈음). 영속화는 M3+.

---

## M2.1 conformance 재검토 (2026-06-02, v1.0.0..HEAD 12 커밋)

momus 적대적 재검토 — v1.0.0(T83 OKAY) 이후 추가분(V-M2-2·backlog 정리·T68 JWT·custom pack 영속화·ETF/리츠 factor). **핵심 보증 전부 유지**: 재현성 freeze(리츠 reference pack 이 빌트인 content_hash·snapshot_versions 무영향 실증, custom pack append-only immutable)·user 격리(IDOR owner-check, import-check canonical-only)·인증 우회 차단(AUTH_SECRET 게이팅·algorithm confusion 방어·signature 보존·함정 D)·빌트인 무영향. V-M2-2/3/4 수정 확정.

발견 이슈:
- **[수정완료 — 이슈 1]** `dividend-yield:reit` 의 `dividends_paid` 부호 미방어(§2.1 Fidelity). K-IFRS 현금흐름표 "재무활동 배당금 지급"은 현금유출이라 음수로 보고될 수 있어 음수 yield(거울 왜곡) 산출 위험. → `db_field_provider.FieldResolution.magnitude`(abs) 정규화 + dividends_paid_annual entry magnitude=True + 음수 회귀 테스트(`test_dividend_yield_abs_normalizes_negative_dividends`). 배당 *규모* 로 해석.
- **[수정완료 — 이슈 2]** JWT `require=["exp"]` 추가(`auth.py` `_decode_jwt`) — exp 없는 서명 토큰의 영구 유효 차단(MissingRequiredClaimError→401) + 회귀 테스트(`test_jwt_without_exp_returns_401`). NextAuth 토큰은 항상 exp 보유하나 게이트가 그 동작에만 의존하지 않게 명시 강제. aud/iss 검증은 운영 claims 확정 시(M3).
- **[수정완료 — 이슈 3]** AUTH_SECRET prod startup 게이트(`config.assert_auth_config_for_environment` + `create_app` 호출) — PROD(SPECULUM_ENV=prod) + AUTH_SECRET 부재 시 RuntimeError(앱 부팅 차단, single-user silent degrade 방지). dev/staging 은 fallback 허용(회귀 0). 7 테스트(`test_auth_prod_gate.py`).

**판정: 이슈 1·2·3 전부 수정 → OKAY(push 가능).** momus M2.1 재검토 발견 이슈 전부 해소.

### ETF NAV 실데이터 — R4 확정 deferred (외부 차단)
2026-06-02 pykrx 실 검증 시도: 일반 주식 OHLCV 는 정상이나 **ETF endpoint 만 빈 응답**(get_etf_ohlcv_by_date "isin" 조회 실패 — KRX ETF 페이지 차단/pykrx 1.2.8 호환 문제). ETF NAV/괴리율/AUM 실데이터 확보 불가 확정 → ETF adapter 골격(커밋 102c018)은 데이터 합류 시 활용, canonical_id·영구화·factor 는 R4 deferred 유지(KRX ETF 접근 가능 환경/credential 필요).

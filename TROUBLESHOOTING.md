# Troubleshooting

본 문서는 Speculum 의 setup / 운영 / 개발 시 자주 발생하는 문제와 해결책의
모음입니다. 새 항목은 PR 로 추가해 주세요. 본 페이지에 없는 문제는 GitHub
Discussions 또는 Issue 에 보고하면 동일 카테고리로 합류 가능.

---

## 1. 환경 setup

### 1.1 `pnpm install` 실패 — Node 버전 불일치

**증상**:
```
ERR_PNPM_UNSUPPORTED_ENGINE  Unsupported engine
```

**원인**: Speculum 은 Node 20 LTS 기준. Node 18 / 22 환경에서 의존성
호환성 문제 발생 가능.

**해결**: nvm / volta / fnm 으로 Node 20 활성화.
```bash
# nvm
nvm install 20 && nvm use 20

# volta
volta install node@20
```

### 1.2 `python -m pip install -e .` 실패 — 컴파일러 누락

**증상** (Windows):
```
error: Microsoft Visual C++ 14.0 or greater is required
```

**원인**: pykrx / psycopg 등 일부 의존성이 wheel 미제공 환경에서 빌드.

**해결**:
- Windows — "Build Tools for Visual Studio" 설치 (C++ workload).
- 또는 Python 3.12 (현재 minimum) wheel 가 모두 제공되는 platform 사용.

### 1.3 PostgreSQL 연결 실패

**증상**:
```
psycopg.OperationalError: connection failed
```

**해결**:
- `DATABASE_URL` 환경변수 설정 확인 — `postgresql+psycopg://user:pass@host:port/dbname`.
- M0 dev 는 SQLite in-memory 도 사용 가능 — `tests/test_db/conftest.py` 패턴 참조.

### 1.4 Windows 의 한글 console mojibake

**증상**: `python tools/check_*.py` 출력의 한글이 `���` 로 깨짐.

**원인**: cmd / PowerShell default encoding = cp949.

**해결**:
- 모든 `tools/check_*.py` 가 stdout/stderr 를 UTF-8 로 reconfigure 함.
  파일 직접 호출은 정상 표시. mojibake 가 보이면 reconfigure 코드가
  적용되지 않은 환경 — Python 3.12 + Windows 10+ 권장.
- PowerShell 의 경우 `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8`
  명시 적용 가능.

---

## 2. 개발 워크플로우

### 2.1 `pnpm test` 가 "React is not defined" 로 실패

**증상**: vitest 의 JSX 변환이 자동 모드 안 됨.

**해결**: `client/vitest.config.ts` 의 `esbuild: { jsx: "automatic" }`
보장. PR-pre validate 시 `pnpm typecheck && pnpm test` 모두 통과 확인.

### 2.2 vitest 의 radix Tooltip 테스트 hang

**증상**: SourceAttribution / Tooltip 관련 테스트 timeout.

**원인**: jsdom 에 ResizeObserver / IntersectionObserver 미구현.

**해결**: `client/vitest.setup.ts` 의 polyfill 활성화 확인. 누락 시
관련 stub 추가.

### 2.3 ruff 가 의도적 패턴 (StrEnum 등) 을 violation 보고

**원인**: M0 의 ruff config 가 일부 rule 의도적 ignore (StrEnum
migration, sessionmaker convention 등).

**해결**: `server/pyproject.toml` 의 `[tool.ruff.lint] ignore` 목록 확인.
새 의도적 패턴은 단일 rule code 단위로 ignore 추가 + 본 파일에 사유 기록.

### 2.4 CI 의 forbidden-words 게이트 실패

**증상**: 새 PR 이 `'<단어>'` 발견으로 reject.

**원인**: `shared/forbidden-words.json` 의 ko_absolute / en_absolute 에
포함된 단어 사용.

**해결**:
- 일반적인 워드 (`Upgrade`, `Bullish` 등) — 다른 표현으로 우회. `pip
  install --upgrade` 같은 의존성 명령은 `-U` short flag 사용.
- 도메인 명사 (`Long`, `Short`, `Hold`) — 같은 의미의 한글 또는 다른
  영문 표현 사용.
- 단어 자체가 docstring 의 *주제* 인 경우 (`forbidden_words.py` 내부 등)
  ADR-0007 D5 의 exemption 검토.

### 2.5 disclaimer-coverage 게이트 실패

**증상**: `client/app/layout.tsx` 변경 후 CI fail.

**원인**: ADR-0006 D2 + ADR-0007 D2 의무 component (`DisclaimerFooter`
+ `ConsentModal`) 의 import 또는 JSX 사용이 layout 에서 사라짐.

**해결**: layout 의 root 에 두 component mount 복원. 단순한 reorder 도
import / JSX 명시되어 있으면 통과.

### 2.6 source-attribution 게이트 실패

**증상**: 새 `.tsx` 파일이 `FactorValue` 사용했으나 wrap import 없어
reject.

**원인**: ADR-0007 D2 의 의무 — factor value 표시는 `SourceAttribution`
직접 wrap 또는 `MetricCard` / `CompareGrid` 위임.

**해결**: 해당 component 가 표시 책임이면 wrap 추가. 표시 책임이 아닌
type-only import 인 경우 `lib/api/` / `lib/factor/` / `__tests__/` 로
위치 이동 (exempt path).

### 2.7 pit-bypass 게이트 실패

**증상**: 새 repository fetch_X 메서드 추가 시 reject.

**원인**: ADR-0008 D5 의무 — historical query 는 `as_of: date` 인자 보유.

**해결**:
- 일반 PIT 쿼리 — `def fetch_X(self, ..., *, as_of: date)` 추가.
- id-based immutable lookup (citation 등) — `# pit-exempt: <reason>` 주석
  추가 (검사 대상 module 인 경우만).

---

## 3. 런타임 / 운영

### 3.1 Screener 실행 결과가 비어있음

**원인**: M0 backend 의 `_fetch_all_active_codes` 는 fixture 의 active
종목 반환 — fixture data 가 비어있거나 as_of 가 fixture 범위 밖.

**해결**:
- as_of 가 fixture 범위 안 (예: 2024-09-30) 인지 AsOfDatePicker 확인.
- M0 는 condition match 가 아닌 fixture 의 active 종목 전체 반환 — T18
  evaluator 합류 전까지 의도된 동작.

### 3.2 Compare 의 "정확 매치가 없습니다" 에러

**원인**: 종목코드 zero-pad 6 자리 매치 실패.

**해결**: 정확한 6 자리 (`005930`) 입력. backend search 결과의 first
match 가 입력 코드와 정확 일치해야 통과 (oracle T39 C1 정책).

### 3.3 Watchlist 의 종목 코드 lineage UUID 표시

**원인**: M0 의 ItemList 가 종목명/코드 lookup 미구현. `code_lineage_id`
UUID 만 표시.

**해결**: M1+ 의 batch fetch 합류 예정 (Recent Runs page 의 follow-up
backlog). 임시로 search 페이지 (T37 Stock Detail) 에서 코드별 조회.

### 3.4 Save Run 후 Recent Runs 에 즉시 안 보임

**원인**: TanStack Query cache stale.

**해결**: SaveRunButton `onSuccess` 가 `["runs","recent"]` invalidate 호출.
정상 동작 시 navigate 후 즉시 표시. 미표시 시 페이지 새로고침.

### 3.5 Run snapshot 의 "재현 변경: N 키" badge

**의미**: ADR-0008 D7-bis 의 versions diff — snapshot 의 `data_versions`
(factor_pack / price_adjustment / dart_account_mapping 등) 가 현재 active
version 과 다름. Run 재실행 시 다른 결과 가능성.

**해결**: 정상 동작. badge tooltip 에 변경된 키 + old → new 표시.
재현이 필요하면 backend 의 snapshot 그 자체를 사용 (M1+ Run 재실행 UI).

---

## 4. CI / 배포

### 4.1 server-ci 가 "Korean encoding" 으로 실패

**원인**: ruff / pytest 의 한글 stderr 가 Windows CI runner 에서
mojibake — Linux runner 에선 무관하지만 Windows runner 사용 시.

**해결**: `.github/workflows/server-ci.yml` 의 `runs-on: ubuntu-latest`
명시 확인. Windows runner 사용은 별도 cycle 검토.

### 4.2 client-ci 의 build size budget 초과

**원인**: 새 의존성 추가 후 First Load JS 가 큼.

**해결**:
- bundle 분석 (`pnpm build --analyze` — 별도 설정 필요).
- dynamic import 로 페이지별 chunk 분리.
- 의존성 trade-off 검토 — 도메인 가치 대비 비용.

### 4.3 develop push 후 다른 작업자의 stale base

**증상**: 다른 작업자가 `git pull --ff-only` 시 non-fast-forward.

**원인**: 누군가 develop 에 force push 한 경우. CLAUDE.md 정책 위반.

**해결**:
- force push 사실 확인 후 stack 정리.
- 정책 위반 분명하면 main 으로 rollback 후 reissue.

---

## 5. 알려진 한계 (M0 의도)

본 항목들은 "버그" 가 아닌 **의도된 M0 scope 외**. 별도 cycle 합류 예정.

- 가격 차트 (Stock Detail / Compare) — T37 / T38 의 phase B.
- Factor evaluator pipeline — T18+ 합류 시 backend 가 실제 condition
  match 평가. M0 는 fixture 반환.
- 종목 검색 autocomplete — M1+ Combobox.
- NextAuth Google OAuth 합류 — T31 잔여. M0 는 SYSTEM_USER_ID single-user.
- Real-time intraday quotes — Open Data 제약 (ADR-0006 §D5).
- 외국 시장 / 미국 / 일본 — KRX-Native 원칙 (10 기둥 §6).

자세한 out-of-scope 항목은 [CHANGELOG.md](CHANGELOG.md) Unreleased 섹션
참조.

---

## 6. 도움이 필요한 곳

| 채널 | 용도 |
|---|---|
| GitHub Discussions | 사용법 질문 / 토론 |
| GitHub Issue | 재현 가능한 버그 |
| Security advisory | 보안 취약점 (private, [SECURITY.md](SECURITY.md)) |
| ADR 제안 | 큰 결정 — `docs/adr/` 패턴 따름 |

질문 전 본 문서와 [CONTRIBUTING.md](CONTRIBUTING.md), [CHANGELOG.md](CHANGELOG.md),
관련 ADR 검색 권장.

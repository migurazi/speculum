# ADR-0006: 법률 검토 결과 정리 (유사투자자문업·데이터 라이선스·개인정보)

| | |
|---|---|
| **Status** | ACCEPTED (M0 무료 운영 한정) — 유료화·기능 확장 시 재검토 의무 |
| **Date** | 2026-05-22 |
| **Deciders** | 사용자 |
| **Reviewers** | librarian (외부 자료 조사). **변호사 직접 자문 미실시** — M0 release 전 권장. |
| **Related** | `docs/CONCEPT.md §5`, `docs/M0_PLAN.md T6`, [[adr-0007-default-ui-rules]] (예정), [[adr-0011-watchlist-scope]] (예정) |

## Context

Speculum 의 운영에 적용되는 한국 법령 / 약관을 정리하여, 다음을 결정한다:

1. **자본시장법 유사투자자문업 등록 의무 — 신고 필요한가?**
2. **데이터 라이선스** — DART, KRX, pykrx, FDR, ECOS 각각의 사용·표시 의무
3. **개인정보보호법** — Google OAuth, 회원 데이터, 만 14세 정책
4. **디스클레이머 / 처리방침 / 이용약관** 의 권장 형식

본 ADR 은 외부 자료 조사 (librarian) 에 기반한 **임시 정책**. M0 release 전 변호사 직접 자문으로 검증 권장.

## Decision

### D1. 유사투자자문업 — 신고 의무 없음 (M0 무료 운영 한정)

**자본시장법 제101조 제1항** 의 3 요건:
1. 투자자문업 미등록자 — ✓ 해당 (Speculum 미등록)
2. **일정한 대가** 수령 — ✗ **해당하지 않음** (M0 = 완전 무료)
3. 불특정 다수에게 개별성 없는 조언 — 회피 가능 (D2~D4)

→ M0 무료 운영 시 **금융위 신고 의무 없음**.

**M1+ 결정 시 ADR 재발행 필수**:
- 유료 구독 / 유료 알림 / 유료 콘텐츠 도입 → 즉시 신고 검토
- 광고 수익만 발생 시 → librarian 보고서 A-3 의 비조치의견서 패턴 따라 신고 불필요로 해석되나 변호사 자문 권장

### D2. "조언" 회피 — 시스템 디자인 제약 (8 기둥 §2.2 No Advice 의 법적 implementation)

금융위·금감원 해석 (2024.8.28 회신) 의 "**금융 관련 지식만 제공하는 수준**" 에 머무름. 다음을 시스템 차원에서 금지:

| 금지 항목 | 이유 | 강제 메커니즘 |
|---|---|---|
| "추천 종목" / "오늘의 종목" / "유망주" 레이블 | 투자판단 조언 | ESLint rule + CI 게이트 (T29 forbidden words) |
| 자동화된 점수 시스템 (예: "AI 점수 87점") | 가치 평가 조언 | 빌트인 factor 에 합산 점수 0 (D8 의 ADR-0007 에서 구체) |
| 푸시 알림 — "지금 매수 적기" / "X 도달, 매수 신호" | 매매 시점 조언 | 푸시 알림 자체 비활성 (M0 — ADR-0011) |
| 기본 정렬 = 가치 시그널 (예: "PER 낮은 순") | 편집 행위 = 조언 | 기본 정렬 = 시가총액·가나다 (중립적) — ADR-0007 |
| 1:1 종목 질의응답 (챗봇 등) | 개별성 있는 조언 → 투자자문업 등록 | 미구현 |

### D3. "대가" 회피 — M0 무료 운영

- M0 = 완전 무료. 광고 X. 후원 X.
- Google OAuth 이메일·프로필 수집은 **금전적 대가 아님** (librarian A-6).
- 초대제·승인제는 접근 제한이지 대가 아님.

**M1+ 변경 시 의무**:
- 광고 도입 → ADR 재발행 + 비조치의견서 패턴 검토
- 유료 도입 → 즉시 신고 검토 + 변호사 자문

### D4. "불특정 다수" vs "특정인" — 회원제의 의미

- Google OAuth 회원제 = "불특정 다수" 범주 (회원 누구나 가입 가능 가정).
- "특정인" 으로 분류되면 투자자문업 등록 필요 → 더 무겁다. 회원제이지만 가입 자유 → 유사투자자문업 범주가 자연.
- M1+ 에 초대제·승인제로 전환 시 "특정인" 으로 해석될 위험 → ADR 재발행.

### D5. 데이터 라이선스 정책

#### D5.1 DART OpenAPI
- **상업적 이용 가능** (공공데이터법 적용).
- **출처 표시 의무** — Footer + 데이터 옆 inline citation 모두에 "출처: 금융감독원 전자공시시스템(DART)".
- API 키 발급 후 사용. Rate limit 준수 (ADR-0003 D6).

#### D5.2 KRX 정보데이터시스템
- **회색지대** — 영리 목적 재배포 조건이 명시 불명.
- **M0 정책**: krxdata@krx.co.kr 에 라이선스 문의 (T6 의 산출물). 답변 받기 전 까지는 pykrx 우회 (코드는 MIT/Apache).
- **출처 표시 의무**: Footer + 데이터 옆 "출처: 한국거래소(KRX) / 데이터 수집 via pykrx".

#### D5.3 pykrx (비공식 wrapper)
- **코드 라이선스**: MIT (저장소 LICENSE 직접 확인 T15).
- **데이터 라이선스**: pykrx README 면책 — "Data copyright belongs to KRX and Naver". **pykrx 사용 ≠ KRX 데이터 라이선스 면제**.
- **요청 부담 자제** — 과도한 요청 시 KRX 가 막을 수 있음. ADR-0003 D6 의 throttling 준수.

#### D5.4 FinanceDataReader
- **코드 라이선스**: MIT.
- **데이터 라이선스**: 원본 (KRX, Naver, Yahoo) 약관 각자 적용. FDR 사용으로 면제되지 않음.

#### D5.5 한국은행 ECOS (M1+)
- 공공데이터법 적용. 상업적 이용 가능 + 출처 표시.
- "출처: 한국은행 경제통계시스템(ECOS)".

#### D5.6 Footer 출처 표시 — 권장 문구

```
데이터 출처: 금융감독원 전자공시시스템(DART), 한국거래소(KRX), 한국은행 ECOS.
DART 데이터는 공공데이터법에 따라 출처 표시 후 이용. KRX 데이터는 KRX 이용약관 준수.
```

### D6. 개인정보보호법 — 의무 항목

#### D6.1 동의 모달 (제15조 + 제22조)

회원가입 시 별도 동의 모달 (Google OAuth 화면만으로는 불충분).

**필수 고지 4 항목**:
1. 수집 항목: 이메일, Google 프로필 이름, 프로필 사진 URL, Google 계정 sub
2. 수집·이용 목적: 회원 인증, 사용자 데이터 (Watchlist, 조건셋, Notes) 저장
3. 보유·이용 기간: 회원 탈퇴 시 또는 1 년 미접속 시 즉시 파기
4. 동의 거부 권리: 거부 시 서비스 이용 불가

만 14세 미만 정책 — D6.4 참조.

#### D6.2 처리방침 (제30조)

별도 페이지 (`/privacy`) 항시 공개. 다음 항목 포함:
- 처리 목적, 처리 항목, 보유·이용 기간, 제3자 제공 (X), 처리 위탁 (호스팅 제공자 — Vercel, Fly.io/Railway 명시), 정보주체 권리·행사 방법, 처리방침 변경 절차, 처리 책임자

#### D6.3 정보주체 권리 (제35, 36, 37조)

- **열람**: 회원의 본인 데이터 열람 (UI 에 "내 정보" 페이지)
- **정정·삭제**: 본인 직접 수정 가능 (Watchlist, Notes). 이메일·이름은 Google 의존이라 수정 불가 표시.
- **처리 정지**: 즉시 비활성화 옵션
- **탈퇴**: 즉시 데이터 파기 (DB 물리 삭제 또는 복구 불가 방식). soft-delete 금지.

#### D6.4 만 14세 미만 정책 (제22조의2)

- 동의 모달에 "만 14세 이상" 확인 체크박스 의무.
- 만 14세 미만 가입 시도 시 차단.
- Google OAuth profile 에 생년월일 정보가 있는 경우 자동 검증 가능, 없으면 사용자 confirm 만.

#### D6.5 호스팅·이전

- M0: Vercel (frontend) + Fly.io 또는 Railway (backend) — 모두 해외 서버.
- 개인정보 국외 이전 동의 항목 별도 추가 필요 — librarian C-2 권고.
- 처리방침에 "본 서비스의 사용자 데이터는 [Vercel: 미국 / Fly.io: 미국 동부] 에 저장됩니다" 명시.

### D7. Disclaimer / 동의 모달 / 이용약관 — 권장 문구

#### D7.1 동의 모달 (한국어)

librarian E-1 의 초안 채택, 약간 보강:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                  Speculum 서비스 이용 안내
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[1] 본 서비스의 성격

Speculum 은 한국 주식 시장의 정량 데이터(재무 지표, 시세, 통계 등)를
탐색하는 도구입니다. **투자 권유, 투자자문, 종목 추천을 제공하지 않습니다.**

표시되는 모든 정보는 정보 제공 목적이며, 자본시장법상 투자 자문 또는
유사투자자문업이 아닙니다.

투자 결정 및 그 결과에 대한 최종 책임은 이용자 본인에게 있습니다.
당사는 데이터의 정확성·완전성을 보증하지 않으며, 정보 이용으로 인한
손실에 대해 법적 책임을 지지 않습니다.

[2] 개인정보 수집·이용 동의

  • 수집 항목: 이메일 주소, Google 프로필 이름·사진, Google 계정 식별자
  • 수집·이용 목적: 회원 인증, 사용자 데이터(관심 종목 등) 저장
  • 보유·이용 기간: 회원 탈퇴 또는 1년 미접속 시 즉시 파기
  • 동의 거부 권리: 거부 시 서비스 이용 불가

  □ 위 개인정보 수집·이용에 동의합니다. (필수)
  □ 본 서비스의 사용자 데이터는 해외(미국)에 저장됨을 이해하고 동의합니다. (필수)

[3] 연령 확인

  □ 본인은 만 14세 이상임을 확인합니다. (필수)

[4] 자세한 내용

  [개인정보처리방침] [이용약관]

  [동의하지 않음]  [동의하고 시작하기]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

#### D7.2 Footer Disclaimer — 항시 표시 (한국어)

```
Speculum 은 정량 데이터 탐색 도구이며 투자 권유·투자자문이 아닙니다.
표시되는 정보는 참고용이며, 투자 결정·결과의 책임은 이용자 본인에게 있습니다.
데이터 출처: 금융감독원 DART, 한국거래소 KRX, 한국은행 ECOS.
```

#### D7.3 영문 버전

librarian E-3, E-4 의 초안 채택. 외국인 회원 (소규모) 대응.

### D8. 시스템 디자인 제약 — D2 의 코드 수준 implementation

ADR-0007 (디폴트 UI 규약) 에서 구체적으로 다룰 항목들:

1. **기본 정렬** = 시가총액 ↓ 또는 종목코드 ↑ (가치 시그널 아닌 중립적 정렬)
2. **하이라이트 색상** = 좋다/나쁘다 의미 부여 금지. 분포·차이만 표현 가능.
3. **푸시 알림** = M0 미구현. M1+ 시 ADR-0011 결정.
4. **금지 어휘 lint** = "추천", "유망", "기대", "Buy", "Sell", "강력 매수", "주목" 등 — `forbidden_words.py` 가 코드·콘텐츠·이메일 검사.
5. **점수 합산 금지** — 빌트인 factor 에 multi-factor score 없음. M2 Factor Lab 에서 사용자가 정의하면 그 정의에 user 책임.

### D9. M0 release 전 변호사 자문 — 권장

본 ADR 은 외부 자료 조사 기반의 임시 정책. M0 release (T47) 전 다음을 변호사에게 확인 권장:

- [ ] D1~D2 의 "무료 운영 한정 신고 불요" 해석이 정확한가
- [ ] D5.2 의 KRX 라이선스 문의 결과 반영
- [ ] D7 의 disclaimer / 동의 모달 / 처리방침 / 이용약관 검토
- [ ] D8 의 시스템 디자인 제약이 충분한가

**비용 추정**: 자본시장법 + 개인정보보호법 통합 자문 → 변호사 1~2 회 미팅, 약 50~150 만원 (사용자 결정). M0 conformance review (T45) 와 함께 수행 권장.

## Rationale

1. **자매 프로젝트 결** — Tessera 의 "의료기기 SaMD 회색지대 정직 회피", Norma 의 "진단 보조 의료기기 아님" disclaimer 패턴을 그대로 차용. Speculum 의 "투자 자문 아님" 도 같은 형식.
2. **§2.2 No Advice 의 법적 implementation** — 단순 disclaimer 가 아닌 시스템 디자인 전반의 제약 (D8) — librarian D-5 의 "디스클레이머 효력 한계" 우려 대응.
3. **§2.5 Open Data Sufficiency 의 의무 면** — 무료 데이터의 라이선스 명확화 (D5).
4. **§2.10 Reproducibility 의 법적 면** — 처리 위탁 + 호스팅 위치 명시 (D6.5) → GDPR 등 외국인 사용자 잠재 대응.
5. **임시 정책 + 변호사 자문 가능성 명시** (D9) — Honest about limits. 자매 프로젝트의 정직성 정책 일관.

## Consequences

### Positive
- **M0 무료 운영 = 신고 불요**, 즉시 진행 가능.
- **D8 시스템 디자인 제약** 이 §2.2 No Advice 의 코드 수준 implementation.
- **출처 표시 의무** 가 §2.1 Fidelity 와 자연 정합.
- **개인정보 처리방침 + 동의 모달** 이 단단히 박힘.

### Negative
- **변호사 자문 미실시** 의 잠재 위험 — M0 release 전 보강 필요 (D9).
- **KRX 라이선스 회색지대** — 답변 받기 전까지 pykrx 우회. 만약 KRX 가 영리 사용 제한 답변 시 데이터 전략 재검토.
- **M1+ 변경 시 ADR 재발행 의무** — 광고·유료·푸시 알림 도입 모두 법적 재검토 필요.
- **D8 가 사용자 기대치와 충돌 가능** — "왜 PER 낮은 순 정렬이 default 가 아닌가?" 같은 사용자 피드백 예상. 도움말로 답변.

### Neutral / Unknown
- **국외 호스팅 동의 항목** — D6.5 의 별도 동의가 필요한가에 대한 법적 명확성 부족. 동의 받기로 default (안전 측면).
- **외국인 사용자 GDPR / CCPA** — Speculum 의 사용자는 한국 위주 가정. 외국인 회원이 늘면 ADR 재검토.

## Alternatives Considered

- **A. 변호사 자문 먼저 받고 ADR 작성** — 가장 안전하나 시간·비용. 현재 자료가 librarian 조사로 충분히 single-track 결정 가능 → M0 진행 + 자문은 release 전 보강 (D9). 선택.
- **B. 신고 등록 (보수적)** — M0 무료라도 미리 신고. 비용 + 운영 부담 + 양방향 영업 금지 (2024.8.14 개정) 의 추가 제약. M0 무료 운영에는 과도. **거부**.
- **C. Disclaimer 만으로 운영** — D8 의 시스템 디자인 제약 없이 disclaimer 모달만 띄움. librarian D-5 의 "디스클레이머 단독 효력 부족" 우려 정면 충돌. **거부**.
- **D. 광고 운영 (수익)** — 비조치의견서 패턴상 광고만으로는 신고 불요로 해석되나, "대가" 요건 회색지대 진입. 모델·UX 가 복잡해짐. M0 에서 미도입. **거부**.

## References

### 1차 법령
- [자본시장과 금융투자업에 관한 법률 제101조 — CaseNote](https://casenote.kr/%EB%B2%95%EB%A0%B9/%EC%9E%90%EB%B3%B8%EC%8B%9C%EC%9E%A5%EA%B3%BC_%EA%B8%88%EC%9C%B5%ED%88%AC%EC%9E%90%EC%97%85%EC%97%90_%EA%B4%80%ED%95%9C_%EB%B2%95%EB%A5%A0/%EC%A0%9C101%EC%A1%B0)
- [국가법령정보센터 — 자본시장법 전문](https://www.law.go.kr/lsInfoP.do?lsiSeq=105908)
- [개인정보 보호법 제15·22·30조 — CaseNote](https://casenote.kr/%EB%B2%95%EB%A0%B9/%EA%B0%9C%EC%9D%B8%EC%A0%95%EB%B3%B4_%EB%B3%B4%ED%98%B8%EB%B2%95)

### 행정 해석
- [금융위·금감원 유사투자자문업 해당여부 해석 (2024.8.28) — CaseNote](https://casenote.kr/%EA%B8%88%EC%9C%B5%EC%9C%84%EC%9B%90%ED%9A%8C%C2%B7%EA%B8%88%EC%9C%B5%EA%B0%90%EB%8F%85%EC%9B%90/f398874829)
- [온라인 방송 비조치의견서 — 금융규제포털](https://better.fsc.go.kr/fsc_new/replyCase/LawreqDetail.do?stNo=11&muNo=171&muGpNo=75&lawreqIdx=3509)
- [유사투자자문업자 관리감독 강화방안 — 금융위 정책 문서](https://www.fsc.go.kr/comm/getFile?srvcId=BBSTY1&upperNo=75847&fileTy=ATTACH&fileNo=8)

### 데이터 라이선스
- [OpenDART 이용약관](https://opendart.fss.or.kr/intro/terms.do)
- [KRX OPEN API 서비스 소개](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO001.jsp) — 라이선스 문의: krxdata@krx.co.kr
- [공공데이터포털 — KRX 상장종목정보](https://www.data.go.kr/data/15094775/openapi.do)
- [pykrx GitHub](https://github.com/sharebook-kr/pykrx) — README 면책 조항
- [FinanceDataReader GitHub](https://github.com/FinanceData/FinanceDataReader)
- [한국은행 ECOS Open API](https://ecos.bok.or.kr/api/)

### 벤치마크 / 가이드
- [유사투자자문업자 목록 — 금감원 FINE](https://fine.fss.or.kr/fine/fncco/invsmCnsut/list.do?menuNo=900046)
- [Investing.com 한국 — 면책 조항](https://kr.investing.com/about-us/)
- [소셜 로그인 시 필수 개인정보 동의 — 캐치시큐](https://www.catchsecu.com/archives/13964)
- [아동·청소년 개인정보 수집 제한 — 캐치시큐](https://www.catchsecu.com/archives/15354)

### 자매 프로젝트
- Tessera `docs/adr/0013-phi-at-rest-encryption.md` — 개인정보 분리 패턴
- Norma `docs/CONCEPT.md §5.5` — 의료기기 disclaimer 패턴 (Speculum 의 투자자문 disclaimer 와 동형)

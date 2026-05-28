/**
 * 면책조항 페이지 — ADR-0006 D7 + ADR-0007 D2.
 *
 * DisclaimerFooter 가 모든 페이지에 표시하는 짧은 disclaimer 의 전문 version.
 * 자본시장법 유사투자자문업 회피 + 데이터 정확성 한계 + 8 기둥 §2.2 No Advice
 * 의 사용자 인지.
 *
 * 관련:
 * - ADR-0006 D2 (자본시장법 회피의 법적 implementation)
 * - ADR-0007 D2 (모든 화면 footer disclaimer 의무)
 * - Momus M0 review V2 (T46 fix) — AC-L-04 미통과 해결
 */

import Link from "next/link";

export const metadata = {
  title: "면책조항 — Speculum",
};

export default function DisclaimerPage(): JSX.Element {
  return (
    <main className="mx-auto max-w-3xl px-6 py-10 text-sm leading-7 text-neutral-800">
      <Link
        href="/"
        className="text-xs text-neutral-500 hover:text-neutral-700"
      >
        ← 홈으로
      </Link>
      <h1 className="mt-3 text-2xl font-semibold text-neutral-900">
        면책조항
      </h1>
      <p className="mt-1 text-xs text-neutral-500">
        시행일: 2026-05-28 (M0 v0.1.0)
      </p>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          1. 본 서비스의 성격
        </h2>
        <p className="mt-2">
          Speculum 은 한국 주식 시장의 정량 데이터를 정직하게 비추는{" "}
          <strong>탐색 도구</strong>입니다. 자본시장법상 투자 자문업 또는
          유사투자자문업이 아닙니다.
        </p>
        <p className="mt-2">
          본 서비스는{" "}
          <strong>
            거래 행위·보유 결정 등 투자 판단에 관한 어떠한 권유·자문·신호도
            제공하지 않습니다.
          </strong>{" "}
          종목·시점·전략에 대한 선택과 책임은 전적으로 이용자 본인에게 있습니다.
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          2. 데이터의 정확성
        </h2>
        <p className="mt-2">
          본 서비스가 표시하는 모든 데이터는{" "}
          <strong>
            금융감독원 DART, 한국거래소 KRX, 한국은행 ECOS 등 1차 자료
          </strong>
          에서 수집됩니다. 모든 지표에는 산출식 · 출처 · 기준일이 함께
          표시됩니다.
        </p>
        <p className="mt-2">
          그러나 외부 출처의 일시 장애, 정정공시, 데이터 변환 오류 등으로 인해
          표시된 값이 실제와 다를 수 있습니다. 운영자는 데이터의 정확성·완전성
          ·시의성을 보증하지 않습니다.
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          3. Point-in-Time 의 한계
        </h2>
        <p className="mt-2">
          본 서비스는 과거 시점 분석 시 그 시점에 알 수 없던 데이터를 사용하지
          않으려 노력합니다 (look-ahead bias 방지). 그러나{" "}
          <strong>분기 재무 데이터의 effective_date 정확도는 일 단위 보수적
          근사</strong>
          이며, DART 공시의 실시간성에 한계가 있습니다. 백테스트나 시뮬레이션
          결과는 본 한계를 고려해 해석하십시오.
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          4. 책임의 한계
        </h2>
        <p className="mt-2">
          이용자는 본 서비스의 정보를 참고용으로만 이용해야 하며, 투자 결정과
          그 결과에 대한 모든 책임은 이용자 본인에게 있습니다. 운영자는 본
          서비스의 정보 이용으로 인해 발생한 직접적 · 간접적 손실에 대해 어떠한
          법적 책임도 지지 않습니다.
        </p>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          5. 데이터 출처
        </h2>
        <p className="mt-2">
          본 서비스는 다음 출처를 사용하며, 각 출처의 이용약관·라이선스를
          준수합니다.
        </p>
        <ul className="mt-2 list-disc pl-6">
          <li>
            금융감독원 전자공시시스템 (DART) — 공공데이터법에 따른 출처 표시
          </li>
          <li>
            한국거래소 (KRX) — pykrx 라이브러리 경유, KRX 이용약관 준수
          </li>
          <li>
            한국은행 경제통계시스템 (ECOS) — M1 합류 예정, 공공데이터법
          </li>
          <li>
            FinanceDataReader — 검증 출처. 원본 데이터의 라이선스 각자 적용
          </li>
        </ul>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-semibold text-neutral-900">
          6. 관련 문서
        </h2>
        <ul className="mt-2 list-disc pl-6">
          <li>
            <Link
              href="/privacy"
              className="font-medium text-neutral-900 underline"
            >
              개인정보처리방침
            </Link>
          </li>
          <li>
            <Link
              href="/terms"
              className="font-medium text-neutral-900 underline"
            >
              이용약관
            </Link>
          </li>
        </ul>
      </section>

      <p className="mt-8 text-xs text-neutral-500">
        본 면책조항은 M0 v0.1.0 시점의 기재이며, 변호사 자문 (ADR-0019) 결과에
        따라 본문이 갱신될 수 있습니다.
      </p>
    </main>
  );
}

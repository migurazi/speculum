/**
 * DisclaimerFooter — ADR-0006 D2/D5 + ADR-0007 D2 의 footer 의무 구현.
 *
 * 모든 화면 하단에 배치 (`app/layout.tsx`). 본문 3 요소:
 *   1. 본 도구는 정보 제공 도구이며 투자 자문이 아님 (ADR-0006 D2).
 *   2. 데이터 출처 표기 — DART / KRX / pykrx / FDR (ADR-0006 D5).
 *   3. 자세한 약관·정책 link (placeholder — M0 release 직전 추가).
 *
 * 8 기둥 §2.2 No Advice 의 사용자 시각 명시 — footer 본문에 금지 어휘
 * (ADR-0007 D4 의 ko_absolute / en_absolute) 사용 불가. ESLint rule (T29)
 * 이 빌드 시 강제.
 *
 * 관련 ADR / 문서:
 * - ADR-0006 D2 (No Advice — 법적 implementation), D5 (데이터 라이선스
 *   출처 의무).
 * - ADR-0007 D2 (모든 화면 footer disclaimer 의무).
 * - M0_PLAN T32 (본 cycle).
 */

import { cn } from "@/lib/utils";

interface DisclaimerFooterProps {
  readonly className?: string;
}

export function DisclaimerFooter({
  className,
}: DisclaimerFooterProps): JSX.Element {
  return (
    <footer
      className={cn(
        "border-t border-neutral-200 px-6 py-6 text-xs text-neutral-600",
        className,
      )}
    >
      <div className="mx-auto max-w-5xl space-y-2">
        <p className="font-medium text-neutral-800">
          본 도구는 정보 제공 목적의 정량 데이터 탐색기이며 투자 자문이
          아닙니다.
        </p>
        <p>
          모든 지표 값은 사용자가 정의한 조건의 필터링 결과를 표시합니다.
          시스템은 종목·시점·전략을 권유하지 않으며, 표시되는 모든 값은 식·출처·
          기준일을 함께 제시합니다.
        </p>
        <p>
          데이터 출처:{" "}
          <span className="font-medium">금융감독원 전자공시시스템(DART)</span>
          {" · "}
          <span className="font-medium">한국거래소(KRX)</span>
          {" · "}
          <span className="font-medium">pykrx</span>
          {" · "}
          <span className="font-medium">FinanceDataReader</span>.
        </p>
        <p className="text-neutral-500">
          개인정보처리방침 · 이용약관 · 면책조항 — M0 release 직전 게시 예정.
        </p>
      </div>
    </footer>
  );
}

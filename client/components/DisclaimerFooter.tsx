/**
 * DisclaimerFooter — ADR-0006 D2/D5 + ADR-0007 D2 의 footer 의무 구현.
 *
 * 모든 화면 하단에 배치 (`app/layout.tsx`). 본문 4 요소:
 *   1. 본 도구는 정보 제공 도구이며 투자 자문이 아님 (ADR-0006 D2).
 *   2. 데이터 출처 표기 — DART / KRX / pykrx / FDR (ADR-0006 D5).
 *   3. 처리방침 · 이용약관 · 면책조항 실 link (T46 V2 fix — Momus M0 review).
 *
 * 8 기둥 §2.2 No Advice 의 사용자 시각 명시 — footer 본문에 금지 어휘
 * (ADR-0007 D4 의 ko_absolute / en_absolute) 사용 불가. CI 게이트 (T29) 가
 * 빌드 시 강제.
 *
 * 관련 ADR / 문서:
 * - ADR-0006 D2 (No Advice — 법적 implementation), D5 (데이터 라이선스
 *   출처 의무).
 * - ADR-0007 D2 (모든 화면 footer disclaimer 의무).
 * - Momus M0 review V2 (Critical, T46 fix) — placeholder 교체.
 * - M0_PLAN T32 (initial scaffold).
 */

import { getTranslations } from "next-intl/server";
import Link from "next/link";

import { cn } from "@/lib/utils";

interface DisclaimerFooterProps {
  readonly className?: string;
}

export async function DisclaimerFooter({
  className,
}: DisclaimerFooterProps): Promise<JSX.Element> {
  // 서버 컴포넌트의 i18n 패턴: getTranslations(네임스페이스).
  const t = await getTranslations("legal");

  return (
    <footer
      className={cn(
        "border-t border-neutral-200 px-6 py-6 text-xs text-neutral-600",
        className,
      )}
    >
      <div className="mx-auto max-w-5xl space-y-2">
        <p className="font-medium text-neutral-800">
          {t("footer.noAdvice")}
        </p>
        <p>
          {t("footer.filteringNote")}
        </p>
        <p>
          {t("footer.sourcesLabel")}{" "}
          <span className="font-medium">{t("footer.sourceDart")}</span>
          {" · "}
          <span className="font-medium">{t("footer.sourceKrx")}</span>
          {" · "}
          <span className="font-medium">{t("footer.sourcePykrx")}</span>
          {" · "}
          <span className="font-medium">{t("footer.sourceFdr")}</span>.
        </p>
        <p className="text-neutral-500">
          <Link
            href="/privacy"
            className="underline-offset-2 hover:text-neutral-800 hover:underline"
          >
            {t("footer.linkPrivacy")}
          </Link>
          {" · "}
          <Link
            href="/terms"
            className="underline-offset-2 hover:text-neutral-800 hover:underline"
          >
            {t("footer.linkTerms")}
          </Link>
          {" · "}
          <Link
            href="/disclaimer"
            className="underline-offset-2 hover:text-neutral-800 hover:underline"
          >
            {t("footer.linkDisclaimer")}
          </Link>
        </p>
      </div>
    </footer>
  );
}

"use client";

/**
 * PublisherClaim — publisher handle claim UI (ADR-0034 D1/D4).
 *
 * 사용자가 handle 을 입력하고 "publisher 등록" 버튼을 누르면
 * POST /api/publishers 로 claim 을 시도한다. 결과:
 *   - 성공: "인증된 publisher: @{handle}" 표시 + v2 namespace 발급 안내.
 *   - 409: "이미 등록된 handle 이거나 계정에 이미 handle 이 등록되어 있습니다" (grayscale).
 *   - 422: "예약된 handle 은 사용할 수 없습니다" (grayscale).
 *   - 기타: 에러 메시지 (grayscale).
 *
 * 시각 정책 (ADR-0028 D3):
 *   - 모든 텍스트·배지: neutral grayscale 계열만 — 판단색(red/green/amber) 0.
 *   - 사실 식별자만 표시 — 권위 신호·순위·인기·자문성 어휘 0.
 *
 * 사용 위치: Factor Lab pack 메타 섹션(app/lab/page.tsx).
 */

import { useMutation } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import { claimPublisher, type Publisher } from "@/lib/api/publishers";

/**
 * claim 에러 → i18n 메시지 선택 헬퍼.
 *
 * 409 → handle/계정 중복 메시지
 * 422 → 예약 handle 메시지
 * 그 외 → generic 메시지(message 포함)
 */
function classifyClaimError(
  err: Error,
  t: ReturnType<typeof useTranslations<"lab">>,
): string {
  if (err instanceof ApiError) {
    if (err.status === 409) return t("publisher.claim.error409");
    if (err.status === 422) return t("publisher.claim.error422");
    return t("publisher.claim.errorGeneric", { message: err.message });
  }
  return t("publisher.claim.errorGeneric", { message: err.message });
}

export function PublisherClaim(): JSX.Element {
  const t = useTranslations("lab");
  const [handle, setHandle] = useState("");
  const [claimError, setClaimError] = useState<string | null>(null);
  const [claimed, setClaimed] = useState<Publisher | null>(null);

  const mutation = useMutation<Publisher, Error, string>({
    mutationFn: (h) => claimPublisher(h),
    onSuccess: (pub) => {
      setClaimed(pub);
      setClaimError(null);
      setHandle("");
    },
    onError: (err) => {
      setClaimError(classifyClaimError(err, t));
    },
  });

  const handleSubmit = (e: React.FormEvent): void => {
    e.preventDefault();
    const trimmed = handle.trim();
    if (!trimmed) return;
    setClaimError(null);
    mutation.mutate(trimmed);
  };

  return (
    <div
      data-testid="publisher-claim"
      className="space-y-2 border-t border-neutral-200 pt-3"
    >
      <p className="text-xs font-medium text-neutral-600">
        {t("publisher.claim.heading")}
      </p>
      <p className="text-[11px] text-neutral-500">
        {t("publisher.claim.description")}
      </p>

      {/* 성공 상태 — 등록된 publisher identity 표시 */}
      {claimed !== null && (
        <div
          data-testid="publisher-claim-success"
          className="space-y-1 rounded border border-neutral-200 bg-neutral-50 px-3 py-2"
        >
          <p className="text-xs font-medium text-neutral-700">
            {t("publisher.identity.verifiedPublisher", { handle: claimed.handle })}
          </p>
          <p className="text-[11px] text-neutral-500">
            {t("publisher.claim.namespaceHint", { handle: claimed.handle })}
          </p>
        </div>
      )}

      {/* 등록 폼 — 성공 후에도 재등록 시도 UI 유지하지 않음(단일 claim) */}
      {claimed === null && (
        <form onSubmit={handleSubmit} className="flex items-center gap-2">
          <input
            type="text"
            value={handle}
            onChange={(e) => setHandle(e.target.value)}
            placeholder={t("publisher.claim.handlePlaceholder")}
            aria-label={t("publisher.claim.handleAriaLabel")}
            data-testid="publisher-claim-input"
            className="min-w-0 flex-1 rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
            disabled={mutation.isPending}
          />
          <button
            type="submit"
            disabled={mutation.isPending || handle.trim().length === 0}
            data-testid="publisher-claim-button"
            className="shrink-0 rounded-md border border-neutral-300 px-3 py-1 text-xs font-medium text-neutral-700 disabled:cursor-not-allowed disabled:opacity-50 hover:bg-neutral-50"
          >
            {mutation.isPending
              ? t("publisher.claim.submitting")
              : t("publisher.claim.submit")}
          </button>
        </form>
      )}

      {/* 에러 — grayscale 중립 톤(판단색 0) */}
      {claimError !== null && (
        <p
          data-testid="publisher-claim-error"
          className="text-[11px] text-neutral-600"
        >
          {claimError}
        </p>
      )}
    </div>
  );
}

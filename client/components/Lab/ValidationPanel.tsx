"use client";

/**
 * ValidationPanel — POST /api/factor-packs/validate 결과 표시.
 *
 * valid 면 통과 배지, 아니면 stage(schema/identity/acyclic/citation/
 * forbidden_vocab) + message 를 issue 목록으로 보여준다. 검증 endpoint 가 권위 —
 * 본 패널은 결과를 중립 톤으로 나열한다 (판단/추천 어휘 없음).
 */

import { AlertCircle, CheckCircle2, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";

import type { ValidationResult } from "@/lib/api/factor-packs";
import { cn } from "@/lib/utils";

interface ValidationPanelProps {
  readonly result: ValidationResult | null;
  readonly isPending: boolean;
  readonly error: string | null;
  readonly className?: string;
}

export function ValidationPanel({
  result,
  isPending,
  error,
  className,
}: ValidationPanelProps): JSX.Element {
  const t = useTranslations("lab");

  return (
    <section className={cn("space-y-2", className)} aria-live="polite">
      <h3 className="text-sm font-semibold text-neutral-900">
        {t("validation.heading")}
      </h3>

      {isPending ? (
        <p className="flex items-center gap-2 text-xs text-neutral-500">
          <Loader2 size={14} className="animate-spin" aria-hidden="true" />
          {t("validation.pending")}
        </p>
      ) : null}

      {/* 검증 endpoint 호출 자체가 실패한 경우 (네트워크/서버) */}
      {error && !isPending ? (
        <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {t("validation.requestError", { message: error })}
        </p>
      ) : null}

      {result && !isPending && !error ? (
        result.valid ? (
          <p className="flex items-center gap-2 rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
            <CheckCircle2 size={16} aria-hidden="true" />
            {t("validation.valid")}
          </p>
        ) : (
          <div className="space-y-2">
            <p className="flex items-center gap-2 text-sm text-red-800">
              <AlertCircle size={16} aria-hidden="true" />
              {t("validation.invalidCount", { count: result.issues.length })}
            </p>
            <ul className="space-y-1">
              {result.issues.map((issue, index) => (
                <li
                  key={`${issue.stage}-${issue.message}-${index}`}
                  className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-900"
                >
                  <span className="mr-2 inline-block rounded bg-red-200 px-1.5 py-0.5 font-mono text-[10px] uppercase text-red-900">
                    {issue.stage}
                  </span>
                  {issue.message}
                </li>
              ))}
            </ul>
          </div>
        )
      ) : null}

      {!result && !isPending && !error ? (
        <p className="text-xs text-neutral-500">{t("validation.idle")}</p>
      ) : null}
    </section>
  );
}

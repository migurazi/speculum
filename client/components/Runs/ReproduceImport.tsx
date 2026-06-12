"use client";

/**
 * ReproduceImport — JSON 파일 업로드 또는 붙여넣기로 Run 재현 검증 (M2 T80 Phase 2).
 *
 * 재현성 §2.10 원칙:
 *   - 결과는 사실(matches/result_codes/hash)만 표시. 등락색·랭킹·추천 0.
 *   - matches 배지는 검증 상태(일치/불일치) — 등락 판단이 아님.
 *     일치: emerald(중립), 불일치: amber(중립).
 *   - user 비종속: 로그인 없이 공유 JSON 을 업로드해 재현 가능.
 *
 * 입력 방식:
 *   1. 파일 업로드 (<input type="file">) — screen-run-*.json.
 *   2. 텍스트 붙여넣기 (<textarea>).
 *   두 방식 중 마지막 변경이 우선.
 *
 * 결과 표시:
 *   - matches 배지 (일치 / 불일치).
 *   - reproduced vs original result_codes 대조 (길이 + 동일 여부).
 *   - result_hash.
 *   - note (있으면).
 *
 * 관련:
 * - M2_PLAN T80 (export/reproduce)
 * - ADR-0008 D7 (Run snapshot freeze)
 */

import { useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { reproduceRun, type ReproduceResult } from "@/lib/api/runs";

/** 재현 결과 표시 — 사실만, 중립 톤. */
function ReproduceResultPanel({
  result,
}: {
  readonly result: ReproduceResult;
}): JSX.Element {
  const t = useTranslations("runs");

  const reproducedSet = new Set(result.reproducedResultCodes);
  const originalSet = new Set(result.originalResultCodes);

  // 재현에만 있는 코드 (원본에 없음).
  const onlyReproduced = result.reproducedResultCodes.filter(
    (c) => !originalSet.has(c),
  );
  // 원본에만 있는 코드 (재현에 없음).
  const onlyOriginal = result.originalResultCodes.filter(
    (c) => !reproducedSet.has(c),
  );

  return (
    <div className="mt-4 rounded-lg border border-neutral-200 bg-white p-4 text-sm">
      {/* matches 배지 — 검증 상태. 등락 판단색 아님. */}
      <div className="mb-3 flex items-center gap-2">
        {result.matches ? (
          <span className="rounded-md border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-800">
            {t("reproduceMatches")}
          </span>
        ) : (
          <span className="rounded-md border border-amber-300 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-900">
            {t("reproduceMismatch")}
          </span>
        )}
        <span className="text-xs text-neutral-500">
          {t("reproduceHash")}: <span className="font-mono">{result.resultHash}</span>
        </span>
      </div>

      {/* result_codes 대조 */}
      <div className="grid grid-cols-2 gap-3 text-xs">
        <div>
          <p className="mb-1 font-medium text-neutral-700">
            {t("reproduceOriginalCodes", {
              count: result.originalResultCodes.length,
            })}
          </p>
          <p className="text-neutral-500">
            {result.originalResultCodes.slice(0, 5).join(", ")}
            {result.originalResultCodes.length > 5
              ? ` … +${result.originalResultCodes.length - 5}`
              : ""}
          </p>
        </div>
        <div>
          <p className="mb-1 font-medium text-neutral-700">
            {t("reproduceReproducedCodes", {
              count: result.reproducedResultCodes.length,
            })}
          </p>
          <p className="text-neutral-500">
            {result.reproducedResultCodes.slice(0, 5).join(", ")}
            {result.reproducedResultCodes.length > 5
              ? ` … +${result.reproducedResultCodes.length - 5}`
              : ""}
          </p>
        </div>
      </div>

      {/* 불일치 상세 — 한쪽에만 있는 코드 */}
      {!result.matches && (onlyOriginal.length > 0 || onlyReproduced.length > 0) && (
        <div className="mt-3 border-t border-neutral-100 pt-3 text-xs text-neutral-600">
          {onlyOriginal.length > 0 && (
            <p>
              {t("reproduceOnlyOriginal", { count: onlyOriginal.length })}:{" "}
              {onlyOriginal.slice(0, 10).join(", ")}
              {onlyOriginal.length > 10 ? ` … +${onlyOriginal.length - 10}` : ""}
            </p>
          )}
          {onlyReproduced.length > 0 && (
            <p className="mt-1">
              {t("reproduceOnlyReproduced", { count: onlyReproduced.length })}:{" "}
              {onlyReproduced.slice(0, 10).join(", ")}
              {onlyReproduced.length > 10 ? ` … +${onlyReproduced.length - 10}` : ""}
            </p>
          )}
        </div>
      )}

      {/* note */}
      {result.note !== null && result.note.length > 0 && (
        <p className="mt-3 border-t border-neutral-100 pt-3 text-xs text-neutral-500">
          {result.note}
        </p>
      )}
    </div>
  );
}

export function ReproduceImport(): JSX.Element {
  const t = useTranslations("runs");

  // 파일 input ref — 프로그램 방식 초기화용.
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 현재 JSON 원문 (파일 또는 붙여넣기).
  const [jsonText, setJsonText] = useState<string>("");

  // 재현 실행 상태.
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ReproduceResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  /** 파일 선택 → FileReader → jsonText 갱신. */
  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>): void {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      setJsonText((ev.target?.result as string) ?? "");
      setResult(null);
      setError(null);
    };
    reader.readAsText(file, "utf-8");
  }

  /** textarea 붙여넣기 / 직접 입력. */
  function handleTextChange(e: React.ChangeEvent<HTMLTextAreaElement>): void {
    setJsonText(e.target.value);
    setResult(null);
    setError(null);
  }

  /** 재현 실행 — JSON parse → reproduceRun → 결과 표시. */
  async function handleReproduce(): Promise<void> {
    if (!jsonText.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      let parsed: unknown;
      try {
        parsed = JSON.parse(jsonText);
      } catch {
        // 잘못된 JSON — 사용자에게 사실 표시.
        setError(t("reproduceInvalidJson"));
        return;
      }
      const res = await reproduceRun(parsed);
      setResult(res);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  /** 초기화 — 입력 + 결과 모두 클리어. */
  function handleReset(): void {
    setJsonText("");
    setResult(null);
    setError(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }

  return (
    <section
      aria-labelledby="reproduce-heading"
      className="mt-8 rounded-lg border border-neutral-200 bg-neutral-50 p-5"
    >
      <h2
        id="reproduce-heading"
        className="mb-1 text-sm font-semibold text-neutral-800"
      >
        {t("reproduceTitle")}
      </h2>
      <p className="mb-4 text-xs text-neutral-500">
        {t("reproduceDescription")}
      </p>

      {/* 파일 업로드 */}
      <label className="mb-2 block text-xs font-medium text-neutral-700">
        {t("reproduceFileLabel")}
        <input
          ref={fileInputRef}
          type="file"
          accept=".json,application/json"
          onChange={handleFileChange}
          className="mt-1 block w-full text-xs text-neutral-600 file:mr-3 file:rounded file:border file:border-neutral-300 file:bg-white file:px-2 file:py-1 file:text-xs file:text-neutral-700 hover:file:bg-neutral-50"
        />
      </label>

      {/* 또는 붙여넣기 */}
      <p className="mb-1 text-xs text-neutral-500">{t("reproduceOrPaste")}</p>
      <textarea
        value={jsonText}
        onChange={handleTextChange}
        rows={5}
        placeholder={t("reproducePastePlaceholder") as string}
        className="w-full rounded border border-neutral-200 bg-white px-3 py-2 font-mono text-xs text-neutral-800 placeholder:text-neutral-400 focus:border-neutral-400 focus:outline-none"
      />

      {/* 액션 버튼 */}
      <div className="mt-3 flex items-center gap-2">
        <button
          type="button"
          disabled={loading || !jsonText.trim()}
          onClick={() => { void handleReproduce(); }}
          className="rounded border border-neutral-700 bg-neutral-800 px-3 py-1 text-xs font-medium text-white hover:bg-neutral-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? t("reproduceRunning") : t("reproduceRun")}
        </button>
        {(jsonText.length > 0 || result !== null) && (
          <button
            type="button"
            onClick={handleReset}
            className="rounded border border-neutral-300 bg-white px-3 py-1 text-xs text-neutral-600 hover:bg-neutral-50"
          >
            {t("reproduceReset")}
          </button>
        )}
      </div>

      {/* 에러 */}
      {error !== null && (
        <div className="mt-3 rounded border border-neutral-300 bg-white px-3 py-2 text-xs text-neutral-700">
          {error}
        </div>
      )}

      {/* 재현 결과 */}
      {result !== null && <ReproduceResultPanel result={result} />}
    </section>
  );
}

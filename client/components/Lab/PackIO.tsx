"use client";

/**
 * PackIO — pack JSON export / import (ADR-0022 D8 / T75).
 *
 * export 흐름:
 *   backend POST /api/factor-packs/export 호출 → content_hash 봉인된 wrapper JSON
 *   다운로드. textarea 는 export 미리보기용 순수 stringify 유지.
 *
 * import 흐름(2-pass):
 *   1. paste/upload → parsePackJson(marker 인지 포함) → importCheckPack(Pass1)
 *   2. conflicts 있으면 ImportConflictResolver → importPack(Pass2 with resolutions)
 *   3. conflicts 0 이면 바로 Pass2 자동 진행 → editor 적용.
 *
 * silent override 금지(Norma §2.3 / D8.4) — 미해결 충돌이 있으면 "적용" 비활성.
 * 사용자가 각 충돌을 명시 선택한 뒤에만 진행.
 *
 * 저장(user-scoped)은 T69 의존 — 본 컴포넌트는 검증·충돌해소·editor 반영까지.
 */

import { Download, Upload } from "lucide-react";
import { useTranslations } from "next-intl";
import { useRef, useState } from "react";

import type {
  ConflictResolution,
  FactorPack,
  ImportCheckResult,
} from "@/lib/api/factor-packs";
import {
  exportPack,
  importCheckPack,
  importPack,
} from "@/lib/api/factor-packs";
import { ImportConflictResolver } from "@/components/Lab/ImportConflictResolver";
import { exportPackJson, parsePackJson } from "@/lib/factor/pack-io";

interface PackIOProps {
  readonly pack: FactorPack;
  readonly onImport: (pack: FactorPack) => void;
}

/** import UI 의 단계. */
type ImportStage =
  | "idle"
  | "checking"
  | "conflicts"
  | "applying"
  | "done"
  | "error";

export function PackIO({ pack, onImport }: PackIOProps): JSX.Element {
  const t = useTranslations("lab");
  const [importText, setImportText] = useState("");
  const [importError, setImportError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // export 상태
  const [isExporting, setIsExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  // import 2-pass 상태
  const [importStage, setImportStage] = useState<ImportStage>("idle");
  const [checkResult, setCheckResult] = useState<ImportCheckResult | null>(null);
  const [resolutions, setResolutions] = useState<
    Record<string, ConflictResolution>
  >({});

  // export 미리보기 (textarea 용 순수 stringify)
  const exported = exportPackJson(pack);

  const onCopy = (): void => {
    void navigator.clipboard?.writeText(exported);
  };

  const onDownload = async (): Promise<void> => {
    setIsExporting(true);
    setExportError(null);
    try {
      const result = await exportPack(pack);
      const blob = new Blob([JSON.stringify(result, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const slug = pack.pack_slug || "factor-pack";
      a.download = `${slug.replace(/[^a-z0-9-]/gi, "_")}-export.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setExportError((err as Error).message);
    } finally {
      setIsExporting(false);
    }
  };

  const resetImport = (): void => {
    setImportStage("idle");
    setCheckResult(null);
    setResolutions({});
    setImportError(null);
  };

  /**
   * Pass1: JSON 파싱 → import-check(dry-run) → 충돌 없으면 Pass2 자동 진행.
   */
  const runPass1 = async (text: string): Promise<void> => {
    const parseResult = parsePackJson(text);
    if (!parseResult.ok || parseResult.pack === undefined) {
      const reason =
        parseResult.error === "not-an-object"
          ? t("io.importErrorNotObject")
          : parseResult.error === "missing-factors"
            ? t("io.importErrorMissingFactors")
            : t("io.importErrorParse");
      setImportError(reason);
      setImportStage("error");
      return;
    }

    setImportError(null);
    setImportStage("checking");
    try {
      // import-check 는 원문 JSON 그대로 전송(wrapper 포함 가능 — backend 가 처리).
      let rawParsed: unknown;
      try {
        rawParsed = JSON.parse(text);
      } catch {
        rawParsed = parseResult.pack;
      }
      const check = await importCheckPack(rawParsed);
      setCheckResult(check);

      if (!check.valid) {
        // 스키마/identity 오류 — import 불가, 오류 표시.
        const msg =
          check.issues.length > 0
            ? check.issues.map((i) => `[${i.stage}] ${i.message}`).join("\n")
            : t("io.importErrorParse");
        setImportError(msg);
        setImportStage("error");
        return;
      }

      if (check.conflicts.length === 0) {
        // 충돌 없음 — Pass2 자동 진행.
        await runPass2(rawParsed, {});
      } else {
        // 충돌 있음 — 사용자 해소 대기.
        setImportStage("conflicts");
        setResolutions({});
      }
    } catch (err) {
      setImportError((err as Error).message);
      setImportStage("error");
    }
  };

  /**
   * Pass2: 충돌 해소 후 import(apply). editor 에 반영.
   */
  const runPass2 = async (
    rawParsed: unknown,
    resolvedMap: Readonly<Record<string, ConflictResolution>>,
  ): Promise<void> => {
    setImportStage("applying");
    try {
      const result = await importPack(rawParsed, resolvedMap);
      if (!result.valid || result.pack === null) {
        const msg =
          result.issues.length > 0
            ? result.issues.map((i) => `[${i.stage}] ${i.message}`).join("\n")
            : t("io.importErrorParse");
        setImportError(msg);
        setImportStage("error");
        return;
      }
      // editor 반영 — content_hash 등 backend 부가 필드 제외한 FactorPack 추출.
      const { content_hash: _unused, ...cleanPack } = result.pack as FactorPack & {
        content_hash?: string;
      };
      void _unused;
      onImport(cleanPack as FactorPack);
      setImportStage("done");
      setImportText("");
    } catch (err) {
      setImportError((err as Error).message);
      setImportStage("error");
    }
  };

  const onApplyConflicts = (): void => {
    if (checkResult === null) return;
    let rawParsed: unknown;
    try {
      rawParsed = JSON.parse(importText);
    } catch {
      rawParsed = null;
    }
    void runPass2(rawParsed, resolutions);
  };

  const onResolutionChange = (
    canonicalId: string,
    resolution: ConflictResolution,
  ): void => {
    setResolutions((prev) => ({ ...prev, [canonicalId]: resolution }));
  };

  const onFileChange = (e: React.ChangeEvent<HTMLInputElement>): void => {
    const file = e.target.files?.[0];
    if (!file) return;
    void file.text().then((text) => {
      setImportText(text);
      resetImport();
    });
  };

  const onStartImport = (): void => {
    resetImport();
    void runPass1(importText);
  };

  const isImportBusy =
    importStage === "checking" || importStage === "applying";

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* export */}
        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-neutral-900">
              {t("io.exportHeading")}
            </h3>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={onCopy}
                className="rounded-md border border-neutral-300 px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-50"
              >
                {t("io.copy")}
              </button>
              <button
                type="button"
                onClick={() => { void onDownload(); }}
                disabled={isExporting}
                className="inline-flex items-center gap-1 rounded-md border border-neutral-300 px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
              >
                <Download size={12} aria-hidden="true" />
                {isExporting ? t("io.exporting") : t("io.download")}
              </button>
            </div>
          </div>
          <textarea
            value={exported}
            readOnly
            aria-label={t("io.exportAriaLabel")}
            rows={12}
            className="w-full rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 font-mono text-[11px] text-neutral-800"
          />
          {exportError !== null && (
            <p className="rounded-md border border-neutral-300 bg-neutral-50 px-3 py-2 text-xs text-neutral-700">
              {t("io.exportError")}: {exportError}
            </p>
          )}
        </section>

        {/* import input */}
        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-neutral-900">
              {t("io.importHeading")}
            </h3>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="inline-flex items-center gap-1 rounded-md border border-neutral-300 px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-50"
              >
                <Upload size={12} aria-hidden="true" />
                {t("io.uploadFile")}
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept="application/json,.json"
                onChange={onFileChange}
                className="hidden"
                aria-label={t("io.uploadAriaLabel")}
              />
              <button
                type="button"
                onClick={onStartImport}
                disabled={importText.trim().length === 0 || isImportBusy}
                className="rounded-md bg-neutral-900 px-2 py-1 text-xs font-medium text-white hover:bg-neutral-800 disabled:opacity-50"
              >
                {importStage === "checking"
                  ? t("io.importChecking")
                  : t("io.apply")}
              </button>
            </div>
          </div>
          <textarea
            value={importText}
            onChange={(e) => {
              setImportText(e.target.value);
              resetImport();
            }}
            placeholder={t("io.importPlaceholder")}
            aria-label={t("io.importAriaLabel")}
            rows={12}
            className="w-full rounded-md border border-neutral-300 px-3 py-2 font-mono text-[11px] text-neutral-800"
          />
          {importStage === "done" && (
            <p className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-xs text-neutral-700">
              {t("io.importDone")}
            </p>
          )}
          {(importStage === "error" || importError !== null) && importStage !== "conflicts" ? (
            <div className="rounded-md border border-neutral-300 bg-neutral-50 px-3 py-2 text-xs text-neutral-700 whitespace-pre-wrap">
              {importError}
            </div>
          ) : null}
        </section>
      </div>

      {/* 충돌 해소 패널 — conflicts 단계에만 표시 */}
      {importStage === "conflicts" && checkResult !== null && (
        <ImportConflictResolver
          conflicts={checkResult.conflicts}
          resolutions={resolutions}
          onResolutionChange={onResolutionChange}
          onApply={onApplyConflicts}
          isApplying={false}
        />
      )}
    </div>
  );
}

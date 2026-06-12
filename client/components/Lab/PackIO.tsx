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
  /**
   * import 완료 콜백. sourceUrl 은 ADR-0032 D3 provenance — URL import 로 들어온
   * 경우의 출처 URL(paste/파일 업로드/수동 편집본은 null). 호출측(Lab page)이
   * provenance 사이드카로 보관해 저장 시 server 에 전달한다.
   */
  readonly onImport: (pack: FactorPack, sourceUrl?: string | null) => void;
}

// ADR-0032 D2 — URL import 가드: pack 크기 상한(문자 기준 ≈ 1MB). 과대 응답·DoS 방어.
// schema 의 factor maxItems=256 기준 정상 pack 은 이보다 훨씬 작다.
const _MAX_PACK_CHARS = 1_000_000;

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
  // ADR-0032 D2 — 봉인 불일치 등 비차단 경고(import 는 계속 진행).
  const [importWarning, setImportWarning] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ADR-0032 D2 — client-fetch URL import 상태.
  const [importUrl, setImportUrl] = useState("");
  const [urlBusy, setUrlBusy] = useState(false);
  // ADR-0032 D3 — provenance. URL fetch 로 importText 를 채운 출처 URL. 사용자가
  // textarea 를 수동 편집하거나 파일 업로드하면 null(더 이상 그 URL 의 충실한
  // 사본이 아니므로 provenance 무효). "적용"(resetImport) 으로는 지워지지 않아야
  // 저장까지 살아남는다.
  const [fetchedUrl, setFetchedUrl] = useState<string | null>(null);

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
    setImportWarning(null);
  };

  /**
   * client-fetch URL import (ADR-0032 D2).
   *
   * 브라우저가 pack URL(GitHub raw 등 CORS 허용 host)을 직접 fetch → **검증 없이
   * import 하지 않는다**: 가져온 텍스트를 textarea(importText)에 채워 사용자가 검토한
   * 뒤 기존 "적용"(`runPass1` → import-check 파이프라인, server 게이트 포함)을 거친다.
   * 백엔드 신규 fetch 경로 0(서버 SSRF surface 미추가). content_hash 검증·식별 충돌·
   * forbidden-words 게이트(외부 pack 메타, ADR-0032 D4)는 import-check 가 담당.
   *
   * 가드: https 만 / 크기 상한 / JSON 형태 사전확인. CORS 미허용 host 는 네트워크
   * 오류로 떨어지며 "파일 내려받아 업로드" 를 안내(known limit, T-M4-03d).
   */
  const onFetchUrl = async (): Promise<void> => {
    const url = importUrl.trim();
    resetImport();
    // 새 fetch 시작 — 이전 provenance 무효화(성공 시 아래에서 재설정).
    setFetchedUrl(null);
    if (!/^https:\/\//i.test(url)) {
      setImportError(t("io.importUrlErrorScheme"));
      setImportStage("error");
      return;
    }
    setUrlBusy(true);
    try {
      const res = await fetch(url, { redirect: "follow" });
      if (!res.ok) {
        setImportError(t("io.importUrlErrorFetch", { status: res.status }));
        setImportStage("error");
        return;
      }
      const text = await res.text();
      if (text.length > _MAX_PACK_CHARS) {
        setImportError(t("io.importUrlErrorTooLarge"));
        setImportStage("error");
        return;
      }
      // JSON 형태만 사전 확인(상세 검증은 server import-check). 깨진 응답 조기 차단.
      try {
        JSON.parse(text);
      } catch {
        setImportError(t("io.importUrlErrorNotJson"));
        setImportStage("error");
        return;
      }
      // 사용자 검토 → 기존 "적용" 으로 import-check 파이프라인 진입(렌더-전-게이트).
      setImportText(text);
      // ADR-0032 D3 — provenance 기록. 이 URL 에서 받은 텍스트 그대로일 때만 유효.
      setFetchedUrl(url);
    } catch {
      // CORS / 네트워크 — known limit. 로컬 파일 업로드 안내.
      setImportError(t("io.importUrlErrorNetwork"));
      setImportStage("error");
    } finally {
      setUrlBusy(false);
    }
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

      // ADR-0032 D2 — 봉인 불일치는 비차단 경고(내용은 검증 통과). import 는 계속
      // 진행하되 "출처 hash 불일치" 를 사용자에게 명시.
      if (check.valid && check.hashMismatch) {
        setImportWarning(t("io.importHashMismatchWarning"));
      }

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
      // ADR-0032 D3 — provenance 동반 전달. URL fetch 사본이면 fetchedUrl, paste/
      // 파일/수동편집이면 null(출처 없음).
      onImport(cleanPack as FactorPack, fetchedUrl);
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
      // 파일 업로드는 URL 출처 아님 — provenance 무효.
      setFetchedUrl(null);
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
          {/* ADR-0032 D2 — client-fetch URL import. 가져온 JSON 을 아래 영역에
              채워 사용자가 검토 후 "적용"(기존 import-check 파이프라인)으로 진입. */}
          <div className="flex gap-2">
            <input
              type="url"
              value={importUrl}
              onChange={(e) => setImportUrl(e.target.value)}
              placeholder={t("io.importUrlPlaceholder")}
              aria-label={t("io.importUrlAriaLabel")}
              className="flex-1 rounded-md border border-neutral-300 px-3 py-1 font-mono text-[11px] text-neutral-800"
            />
            <button
              type="button"
              onClick={() => { void onFetchUrl(); }}
              disabled={importUrl.trim().length === 0 || urlBusy}
              className="whitespace-nowrap rounded-md border border-neutral-300 px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
            >
              {urlBusy ? t("io.importUrlFetching") : t("io.importUrlFetch")}
            </button>
          </div>
          <p className="text-[11px] text-neutral-400">{t("io.importUrlHint")}</p>
          <textarea
            value={importText}
            onChange={(e) => {
              setImportText(e.target.value);
              resetImport();
              // 수동 편집 — 더 이상 fetch 한 URL 의 충실한 사본 아님(provenance 무효).
              setFetchedUrl(null);
            }}
            placeholder={t("io.importPlaceholder")}
            aria-label={t("io.importAriaLabel")}
            rows={12}
            className="w-full rounded-md border border-neutral-300 px-3 py-2 font-mono text-[11px] text-neutral-800"
          />
          {/* ADR-0032 D2 — 봉인 불일치 비차단 경고(import 진행과 무관하게 표시). */}
          {importWarning !== null && (
            <p
              role="alert"
              className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800"
            >
              {importWarning}
            </p>
          )}
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

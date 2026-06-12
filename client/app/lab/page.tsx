"use client";

/**
 * Factor Lab — 정량 factor pack 정의·검증·export 화면 (M2 T71 Phase 2).
 *
 * 사용자가 Primary→Derived→Composite DAG 형태의 factor pack 을 정의하고,
 * backend 검증 endpoint(POST /api/factor-packs/validate)로 무결성을 확인한 뒤,
 * 전체 pack JSON 을 export/import 한다.
 *
 * 범위 경계 (No Advice / ADR-0022):
 *   본 화면은 "정의 + 검증 + 평가 미리보기 + export" 다. T74 Phase 2 에서 저장 없는
 *   평가 미리보기(EvaluatePreview)를 추가했으나, Composite score 출력은 **시각
 *   게이트**(중립 톤·등락색 0·랭킹 0·입력 순서 유지) 안에서만 노출된다 — 값은
 *   사실로만 표시하고 판단/추천 어휘·순위 표·등락 색상은 일절 없다.
 *
 * 실시간 검증:
 *   pack 정의 변경 시 디바운스 후 자동 검증. "지금 검증" 버튼으로 즉시 호출도 가능.
 */

import { useMutation } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useRef, useState } from "react";

import { CommunityPackBrowser } from "@/components/Lab/CommunityPackBrowser";
import { EvaluatePreview } from "@/components/Lab/EvaluatePreview";
import { FactorForm } from "@/components/Lab/FactorForm";
import { PackAttribution } from "@/components/Lab/PackAttribution";
import { PackIO } from "@/components/Lab/PackIO";
import { PackLibrary } from "@/components/Lab/PackLibrary";
import { PublisherClaim } from "@/components/Lab/PublisherClaim";
import { ReferencePackPicker } from "@/components/Lab/ReferencePackPicker";
import { ValidationPanel } from "@/components/Lab/ValidationPanel";
import {
  validateFactorPack,
  type FactorDef,
  type FactorPack,
  type ValidationResult,
} from "@/lib/api/factor-packs";
import { useAsOfStore } from "@/state/as-of-store";

/** 검증 디바운스 — 입력 중 과도한 호출 차단. */
const VALIDATE_DEBOUNCE_MS = 600;

/** 새 factor 골격 — Primary field 참조 기본값. uuid 는 생성. */
function makeEmptyFactor(): FactorDef {
  return {
    canonical_id: "",
    uuid: generateUuid(),
    name: "",
    description: "",
    unit: "ratio",
    tags: [],
    formula: { ast: { field: "" }, inputs: [] },
  };
}

/** uuid v4 — 브라우저 crypto 우선, 미지원 시 폴백. */
function generateUuid(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // 폴백 — 서버 검증이 형식을 최종 판정. (테스트/구형 환경)
  return "00000000-0000-4000-8000-000000000000";
}

/** 초기 빈 pack — custom user pack slug 예시 형태. */
function makeInitialPack(): FactorPack {
  return {
    pack_slug: "user/my-pack",
    version: "0.1.0",
    factors: [makeEmptyFactor()],
    citation: { title: "" },
  };
}

export default function LabPage(): JSX.Element {
  const t = useTranslations("lab");
  const asOf = useAsOfStore((s) => s.asOf);
  const [pack, setPack] = useState<FactorPack>(makeInitialPack);
  const [result, setResult] = useState<ValidationResult | null>(null);
  // ADR-0032 D3 — import provenance 사이드카(출처 URL). URL import 로 들어온 pack
  // 만 값을 가지며 저장 시 server 로 전달된다. **pack body 와 분리**(content_hash
  // 봉인 무관 — row 메타). 사용자가 pack 을 편집하면 null(더 이상 충실한 사본
  // 아님). 다른 경로(community/reference/저장 pack 불러오기)도 null.
  const [sourceUrl, setSourceUrl] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  /**
   * 사용자 편집 setter — pack 변경 + provenance 무효화. import 출처 URL 의 충실한
   * 사본이 편집되면 그 URL 을 출처로 주장할 수 없으므로 sourceUrl 을 비운다.
   */
  const editPack = (updater: (prev: FactorPack) => FactorPack): void => {
    setPack(updater);
    setSourceUrl(null);
  };

  const mutation = useMutation<ValidationResult, Error, FactorPack>({
    mutationFn: (p) => validateFactorPack(p),
    onSuccess: (r) => setResult(r),
  });

  // mutate 는 react-query 가 안정 참조를 보장하지 않으므로 ref 로 최신값 고정.
  const mutateRef = useRef(mutation.mutate);
  mutateRef.current = mutation.mutate;

  const runValidate = useCallback((p: FactorPack): void => {
    mutateRef.current(p);
  }, []);

  // pack 변경 시 디바운스 자동 검증.
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      runValidate(pack);
    }, VALIDATE_DEBOUNCE_MS);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [pack, runValidate]);

  const updateFactor = (index: number, next: FactorDef): void => {
    editPack((prev) => ({
      ...prev,
      factors: prev.factors.map((f, i) => (i === index ? next : f)),
    }));
  };

  const addFactor = (): void => {
    editPack((prev) => ({ ...prev, factors: [...prev.factors, makeEmptyFactor()] }));
  };

  const removeFactor = (index: number): void => {
    editPack((prev) => ({
      ...prev,
      factors: prev.factors.filter((_, i) => i !== index),
    }));
  };

  const patchPack = (p: Partial<FactorPack>): void => {
    editPack((prev) => ({ ...prev, ...p }));
  };

  const patchCitation = (p: Partial<FactorPack["citation"]>): void => {
    editPack((prev) => ({ ...prev, citation: { ...prev.citation, ...p } }));
  };

  return (
    <main className="mx-auto max-w-5xl px-6 py-8">
      <h1 className="text-xl font-semibold text-neutral-900">{t("title")}</h1>
      <p className="mt-1 text-sm text-neutral-600">{t("description")}</p>

      {/* pack 메타 */}
      <section className="mt-6 space-y-3 rounded-lg border border-neutral-200 bg-neutral-50 p-4">
        <h2 className="text-sm font-semibold text-neutral-900">{t("pack.heading")}</h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="block text-xs font-medium text-neutral-600">
            {t("pack.slug")}
            <input
              type="text"
              value={pack.pack_slug}
              onChange={(e) => patchPack({ pack_slug: e.target.value })}
              placeholder={t("pack.slugPlaceholder")}
              className="mt-1 w-full rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
            />
          </label>
          <label className="block text-xs font-medium text-neutral-600">
            {t("pack.version")}
            <input
              type="text"
              value={pack.version}
              onChange={(e) => patchPack({ version: e.target.value })}
              placeholder="0.1.0"
              className="mt-1 w-full rounded border border-neutral-300 px-2 py-1 font-mono text-xs"
            />
          </label>
          <label className="block text-xs font-medium text-neutral-600 sm:col-span-2">
            {t("pack.citationTitle")}
            <input
              type="text"
              value={pack.citation.title}
              onChange={(e) => patchCitation({ title: e.target.value })}
              placeholder={t("pack.citationTitlePlaceholder")}
              className="mt-1 w-full rounded border border-neutral-300 px-2 py-1 text-xs"
            />
          </label>
        </div>

        {/* ADR-0006 D5 / ADR-0032 #6 — 출처·라이선스·provenance 표시(use-시점).
            license 는 editor 핵심 필드 밖이라 런타임 body 에서 방어적으로 읽고,
            provenance(source_url)는 사이드카에서 가져온다. 표시할 게 있을 때만
            렌더 — 중립 톤(큐레이션/판단 신호 0).
            v2 slug(@{publisher}/{slug}) 면 identity+disclosure 추가(ADR-0034 D7). */}
        <PackAttribution pack={pack} sourceUrl={sourceUrl} />

        {/* ADR-0034 D1/D4 — publisher handle claim. v2 namespace 발급 진입점.
            인증 OAuth 계정 기반 handle 등록 → @{handle}/{slug} pack 발급 가능. */}
        <PublisherClaim />
      </section>

      {/* factor 목록 */}
      <section className="mt-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-neutral-900">
            {t("factors.heading", { count: pack.factors.length })}
          </h2>
          <button
            type="button"
            onClick={addFactor}
            className="inline-flex items-center gap-1 rounded-md border border-neutral-300 px-3 py-1.5 text-xs font-medium text-neutral-700 hover:bg-neutral-50"
          >
            <Plus size={14} aria-hidden="true" />
            {t("factors.add")}
          </button>
        </div>

        {pack.factors.length === 0 ? (
          <p className="text-xs text-neutral-500">{t("factors.empty")}</p>
        ) : (
          pack.factors.map((factor, index) => (
            <FactorForm
              key={factor.uuid || index}
              factor={factor}
              index={index}
              onChange={(next) => updateFactor(index, next)}
              onRemove={() => removeFactor(index)}
            />
          ))
        )}
      </section>

      {/* 검증 */}
      <section className="mt-6 space-y-3 rounded-lg border border-neutral-200 bg-neutral-50 p-4">
        <div className="flex items-center justify-between">
          <ValidationPanel
            result={result}
            isPending={mutation.isPending}
            error={mutation.isError ? mutation.error.message : null}
            className="flex-1"
          />
        </div>
        <button
          type="button"
          onClick={() => runValidate(pack)}
          disabled={mutation.isPending}
          className="inline-flex items-center rounded-md bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-neutral-300 hover:bg-neutral-800"
        >
          {t("validation.runNow")}
        </button>
      </section>

      {/* 평가 미리보기 — 저장 없이 즉시 평가(T74 Phase 2). 중립 표·입력 순서. */}
      <section className="mt-6 space-y-3 rounded-lg border border-neutral-200 bg-neutral-50 p-4">
        <EvaluatePreview pack={pack} asOf={asOf} />
      </section>

      {/* 참조 factor pack 불러오기 (ADR-0023 D8) */}
      <section className="mt-6 space-y-3 rounded-lg border border-neutral-200 bg-neutral-50 p-4">
        <h2 className="text-sm font-semibold text-neutral-900">
          {t("referencePacks.heading")}
        </h2>
        <ReferencePackPicker
          onLoad={(body) => {
            // 참조 pack 은 외부 URL 출처 아님 — provenance 없음.
            setPack(body);
            setSourceUrl(null);
          }}
        />
      </section>

      {/* community pack 탐색 + import (ADR-0028) */}
      <section className="mt-6 space-y-3 rounded-lg border border-neutral-200 bg-neutral-50 p-4">
        <h2 className="text-sm font-semibold text-neutral-900">
          {t("community.heading")}
        </h2>
        <CommunityPackBrowser
          onImport={(p) => {
            // community import 는 in-DB 출처(외부 URL 아님) — provenance 없음.
            setPack(p);
            setSourceUrl(null);
          }}
        />
      </section>

      {/* 저장 / 목록 / 불러오기 */}
      <section className="mt-6 space-y-3 rounded-lg border border-neutral-200 bg-neutral-50 p-4">
        <h2 className="text-sm font-semibold text-neutral-900">{t("library.heading")}</h2>
        <PackLibrary
          pack={pack}
          sourceUrl={sourceUrl}
          onLoad={(p) => {
            // 저장 pack 불러오기 — editor 상태로 들어오면 새 편집 대상. provenance
            // 는 저장 목록에 별도 표시되므로 editor 사이드카는 비운다.
            setPack(p);
            setSourceUrl(null);
          }}
        />
      </section>

      {/* export / import */}
      <section className="mt-6 space-y-3 rounded-lg border border-neutral-200 bg-white p-4">
        <h2 className="text-sm font-semibold text-neutral-900">{t("io.heading")}</h2>
        <PackIO
          pack={pack}
          onImport={(p, url) => {
            // ADR-0032 D3 — URL import 면 출처 URL 을 provenance 사이드카에 보관
            // (저장 시 server 전달). paste/파일/수동편집은 null.
            setPack(p);
            setSourceUrl(url ?? null);
          }}
        />
      </section>
    </main>
  );
}

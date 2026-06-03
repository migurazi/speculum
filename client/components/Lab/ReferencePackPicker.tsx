"use client";

/**
 * ReferencePackPicker — reference pack 목록 표시 및 편집기 적재 UI.
 *
 * GET /api/factor-packs/reference(익명) 로 community reference pack 목록을
 * 불러온 뒤 각 pack 의 slug·version·factorCount·factor 이름을 중립 톤으로
 * 표시하고 "불러오기" 버튼으로 editor 에 적재한다.
 *
 * 시각 정책 (No Advice / ADR-0022 D4 / ADR-0023 D8):
 *   - 모든 텍스트·배지: neutral grayscale 계열만 — 판단색·추천 어휘 0.
 *   - 랭킹/인기/추천 라벨 절대 금지.
 *   - packSlug·version·factorCount·factor 이름 사실 그대로 표시.
 *
 * 관련: PackLibrary.tsx (onLoad 콜백 패턴 동일), factor-packs.ts listReferencePacks.
 */

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";

import type { FactorPack, ReferencePackItem } from "@/lib/api/factor-packs";
import { listReferencePacks } from "@/lib/api/factor-packs";

interface ReferencePackPickerProps {
  /**
   * 불러오기 완료 콜백 — PackLibrary 의 onLoad, PackIO 의 onImport 와 동일 시그니처.
   * 호출자(LabPage)가 setPack(pack) 으로 editor 상태를 교체한다.
   */
  readonly onLoad: (body: FactorPack) => void;
}

/** query key — 참조 pack 목록 캐시. */
const REFERENCE_PACKS_QUERY_KEY = ["factor-packs", "reference"] as const;

/**
 * 톤 상수 — neutral grayscale 계열만(No Advice / ADR-0022 D4).
 * 시각 게이트 테스트가 이 상수를 직접 import 해 검증한다.
 */
export const REF_PACK_LOAD_BTN_CLASS =
  "rounded border border-neutral-300 px-2 py-0.5 text-neutral-700 hover:bg-neutral-50 disabled:opacity-50";
export const REF_PACK_ROW_CLASS =
  "border-b border-neutral-100 last:border-0";
export const REF_PACK_FACTOR_CHIP_CLASS =
  "rounded bg-neutral-100 px-1.5 py-0.5 font-mono text-neutral-600";

export function ReferencePackPicker({
  onLoad,
}: ReferencePackPickerProps): JSX.Element {
  const t = useTranslations("lab");

  const listQuery = useQuery({
    queryKey: REFERENCE_PACKS_QUERY_KEY,
    queryFn: ({ signal }) => listReferencePacks(signal),
  });

  const packs = listQuery.data?.packs ?? [];

  return (
    <div className="space-y-3">
      <p className="text-xs text-neutral-500">
        {t("referencePacks.description")}
      </p>

      {listQuery.isPending ? (
        <p className="text-xs text-neutral-500">
          {t("referencePacks.loading")}
        </p>
      ) : listQuery.isError ? (
        <p className="text-xs text-neutral-700">
          {t("referencePacks.listError", {
            message: (listQuery.error as Error).message,
          })}
        </p>
      ) : packs.length === 0 ? (
        <p
          data-testid="reference-pack-empty"
          className="text-xs text-neutral-500"
        >
          {t("referencePacks.listEmpty")}
        </p>
      ) : (
        <div
          className="overflow-x-auto rounded-md border border-neutral-200"
          data-testid="reference-pack-list"
        >
          <table className="w-full table-fixed text-xs">
            <thead>
              <tr className="border-b border-neutral-200 bg-neutral-50">
                <th className="px-3 py-2 text-left font-medium text-neutral-600 w-2/5">
                  {t("referencePacks.colSlug")}
                </th>
                <th className="px-3 py-2 text-left font-medium text-neutral-600 w-1/6">
                  {t("referencePacks.colVersion")}
                </th>
                <th className="px-3 py-2 text-right font-medium text-neutral-600 w-[10%]">
                  {t("referencePacks.colFactorCount")}
                </th>
                <th className="px-3 py-2 text-left font-medium text-neutral-600">
                  {t("referencePacks.colFactors")}
                </th>
                <th className="px-3 py-2 text-right font-medium text-neutral-600 w-auto">
                  {""}
                </th>
              </tr>
            </thead>
            <tbody>
              {packs.map((item) => (
                <ReferencePackRow
                  key={item.packSlug}
                  item={item}
                  loadLabel={t("referencePacks.loadButton")}
                  onLoad={() => {
                    const { content_hash: _unused, ...cleanBody } =
                      item.body as FactorPack & { content_hash?: string };
                    void _unused;
                    onLoad(cleanBody as FactorPack);
                  }}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Row 서브컴포넌트 ──────────────────────────────────────────────────────────

interface ReferencePackRowProps {
  readonly item: ReferencePackItem;
  readonly loadLabel: string;
  readonly onLoad: () => void;
}

function ReferencePackRow({
  item,
  loadLabel,
  onLoad,
}: ReferencePackRowProps): JSX.Element {
  return (
    <tr
      className={REF_PACK_ROW_CLASS}
      data-testid={`reference-pack-row-${item.packSlug}`}
    >
      <td className="px-3 py-2 font-mono text-neutral-800 truncate">
        {item.packSlug}
      </td>
      <td className="px-3 py-2 font-mono text-neutral-700">{item.version}</td>
      <td className="px-3 py-2 text-right text-neutral-700">
        {item.factorCount}
      </td>
      <td className="px-3 py-2">
        <div className="flex flex-wrap gap-1">
          {item.body.factors.map((f) => (
            <span
              key={f.canonical_id}
              className={REF_PACK_FACTOR_CHIP_CLASS}
              data-testid={`reference-factor-chip-${f.canonical_id}`}
            >
              {f.name}
            </span>
          ))}
        </div>
      </td>
      <td className="px-3 py-2 text-right">
        <button
          type="button"
          onClick={onLoad}
          className={REF_PACK_LOAD_BTN_CLASS}
          data-testid={`reference-pack-load-${item.packSlug}`}
        >
          {loadLabel}
        </button>
      </td>
    </tr>
  );
}

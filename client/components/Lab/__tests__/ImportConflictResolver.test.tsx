/**
 * ImportConflictResolver 단위 테스트 — ADR-0022 D8 / T75.
 *
 * 검증 항목:
 *   (a) canonical tier 충돌(allowed_resolutions 에 replace 없음) → replace 옵션 부재.
 *   (b) 미해결 충돌 존재 시 "적용" 버튼 disabled (D8.4).
 *   (c) rename 선택 시 new_canonical_id 입력 노출.
 *   (d) 시각 게이트 — tier 배지는 grayscale(neutral) 계열, 등락색·추천어휘 0.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ImportConflict } from "@/lib/api/factor-packs";
import { renderWithIntl } from "@/test-utils/intl";
import {
  ImportConflictResolver,
  TIER_BADGE_CLASS,
} from "../ImportConflictResolver";

/** 허용되지 않는 의미색 토큰(등락색/추천 계열) — neutral 이 아닌 색. */
const FORBIDDEN_SEMANTIC_COLOR_TOKENS: ReadonlyArray<string> = [
  "red",
  "rose",
  "blue",
  "sky",
  "indigo",
  "green",
  "emerald",
  "teal",
  "lime",
  "amber",
  "orange",
  "yellow",
  "purple",
  "violet",
  "fuchsia",
  "pink",
  "cyan",
];

/** canonical tier 충돌 — allowed_resolutions 에 replace 없음 (D8.2). */
const BUILTIN_CONFLICT: ImportConflict = {
  canonicalId: "per:ttm",
  incomingHash: "aabbccdd11223344",
  existingHash: "11223344aabbccdd",
  existingTier: "speculum-builtin",
  allowedResolutions: ["skip", "rename"],
};

/** community/custom 충돌 — replace 포함. */
const COMMUNITY_CONFLICT: ImportConflict = {
  canonicalId: "user/per-custom",
  incomingHash: "deadbeef12345678",
  existingHash: "12345678deadbeef",
  existingTier: "community",
  allowedResolutions: ["skip", "rename", "replace"],
};

function renderResolver(
  conflicts: ReadonlyArray<ImportConflict>,
  resolutions: Record<string, import("@/lib/api/factor-packs").ConflictResolution> = {},
  onApply: () => void = vi.fn(),
  onResolutionChange: (
    canonicalId: string,
    resolution: import("@/lib/api/factor-packs").ConflictResolution,
  ) => void = vi.fn(),
): void {
  renderWithIntl(
    <ImportConflictResolver
      conflicts={conflicts}
      resolutions={resolutions}
      onResolutionChange={onResolutionChange}
      onApply={onApply}
    />,
  );
}

describe("(a) canonical tier 충돌 — replace 옵션 부재/비활성 (D8.2)", () => {
  it("builtin 충돌에서 replace 라디오 버튼이 없다", () => {
    renderResolver([BUILTIN_CONFLICT]);
    // skip / rename 은 있어야 함.
    expect(screen.getByLabelText("건너뛰기")).toBeInTheDocument();
    expect(screen.getByLabelText("이름 변경")).toBeInTheDocument();
    // replace 는 없어야 함.
    expect(screen.queryByLabelText("덮어쓰기")).toBeNull();
  });

  it("community 충돌에서 replace 라디오 버튼이 있다", () => {
    renderResolver([COMMUNITY_CONFLICT]);
    expect(screen.getByLabelText("덮어쓰기")).toBeInTheDocument();
  });
});

describe("(b) 미해결 충돌 존재 시 적용 버튼 disabled (D8.4)", () => {
  it("해소 없으면 적용 버튼 disabled", () => {
    renderResolver([BUILTIN_CONFLICT]);
    const btn = screen.getByRole("button", { name: "적용" });
    expect(btn).toBeDisabled();
  });

  it("모든 충돌이 해소되면 적용 버튼 활성", () => {
    renderResolver(
      [BUILTIN_CONFLICT],
      { "per:ttm": { action: "skip" } },
    );
    const btn = screen.getByRole("button", { name: "적용" });
    expect(btn).not.toBeDisabled();
  });

  it("충돌 여럿 — 하나라도 미해결이면 disabled", () => {
    renderResolver(
      [BUILTIN_CONFLICT, COMMUNITY_CONFLICT],
      { "per:ttm": { action: "skip" } },
      // community 충돌 미해결
    );
    expect(screen.getByRole("button", { name: "적용" })).toBeDisabled();
  });

  it("충돌 여럿 — 모두 해소되면 활성", () => {
    renderResolver(
      [BUILTIN_CONFLICT, COMMUNITY_CONFLICT],
      {
        "per:ttm": { action: "skip" },
        "user/per-custom": { action: "replace" },
      },
    );
    expect(screen.getByRole("button", { name: "적용" })).not.toBeDisabled();
  });
});

describe("(c) rename 선택 시 new_canonical_id 입력 노출", () => {
  it("rename 미선택 시 입력창 없음", () => {
    renderResolver([BUILTIN_CONFLICT]);
    expect(screen.queryByLabelText("새 canonical ID 입력")).toBeNull();
  });

  it("rename 선택 시 입력창 표시", () => {
    renderResolver(
      [BUILTIN_CONFLICT],
      { "per:ttm": { action: "rename", newCanonicalId: "" } },
    );
    expect(screen.getByLabelText("새 canonical ID 입력")).toBeInTheDocument();
  });

  it("rename + new_canonical_id 비어 있으면 여전히 disabled", () => {
    renderResolver(
      [BUILTIN_CONFLICT],
      { "per:ttm": { action: "rename", newCanonicalId: "" } },
    );
    expect(screen.getByRole("button", { name: "적용" })).toBeDisabled();
  });

  it("rename + new_canonical_id 채우면 활성", () => {
    renderResolver(
      [BUILTIN_CONFLICT],
      { "per:ttm": { action: "rename", newCanonicalId: "user/per-renamed" } },
    );
    expect(screen.getByRole("button", { name: "적용" })).not.toBeDisabled();
  });

  it("rename 클릭 → onResolutionChange 호출", async () => {
    const onChange = vi.fn();
    renderResolver([BUILTIN_CONFLICT], {}, vi.fn(), onChange);
    const user = userEvent.setup();
    await user.click(screen.getByLabelText("이름 변경"));
    expect(onChange).toHaveBeenCalledWith("per:ttm", {
      action: "rename",
      newCanonicalId: undefined,
    });
  });
});

describe("(d) 시각 게이트 — tier 배지 grayscale, 등락색·추천어휘 0", () => {
  it("TIER_BADGE_CLASS 가 neutral 계열만 포함한다", () => {
    expect(TIER_BADGE_CLASS).toMatch(/neutral/);
  });

  it("TIER_BADGE_CLASS 에 의미색(등락색/추천색) 토큰이 없다", () => {
    for (const token of FORBIDDEN_SEMANTIC_COLOR_TOKENS) {
      expect(TIER_BADGE_CLASS).not.toMatch(new RegExp(`-${token}-|^${token}-|\\b${token}\\b`));
    }
  });

  it("렌더된 충돌 항목에 '추천/인기/유망' 텍스트가 없다", () => {
    renderResolver([BUILTIN_CONFLICT, COMMUNITY_CONFLICT]);
    const list = screen.getByRole("list");
    expect(within(list).queryByText(/추천|인기|유망/)).toBeNull();
  });

  it("적용 버튼 텍스트가 '적용'이며 추천/인기 어휘가 없다", () => {
    renderResolver([BUILTIN_CONFLICT], { "per:ttm": { action: "skip" } });
    const btn = screen.getByRole("button", { name: "적용" });
    expect(btn.textContent).toBe("적용");
    expect(btn.textContent).not.toMatch(/추천|인기|유망/);
  });
});

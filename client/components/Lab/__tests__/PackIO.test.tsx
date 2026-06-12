/**
 * PackIO — client-fetch URL import 테스트 (ADR-0032 D2 / M4 #3).
 *
 * URL 가져오기는 브라우저 fetch 로 pack JSON 을 받아 textarea(importText)에 채우고,
 * 사용자가 검토 후 기존 "적용"(import-check 파이프라인)으로 진입한다. 본 테스트는
 * fetch 를 mock 해 가드(https/크기/JSON/네트워크)와 성공 시 textarea 반영을 검증한다.
 * (import-check/server 게이트는 별도 server 테스트가 커버.)
 */

import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { FactorPack } from "@/lib/api/factor-packs";
import { importCheckPack, importPack } from "@/lib/api/factor-packs";
import { renderWithIntl } from "@/test-utils/intl";

import { PackIO } from "../PackIO";

// import-check/import 파이프라인은 mock — 본 테스트는 fetch 가드 + provenance
// 전달(ADR-0032 D3)만 검증(server 게이트는 server 테스트가 커버).
vi.mock("@/lib/api/factor-packs", async (importActual) => {
  const actual =
    await importActual<typeof import("@/lib/api/factor-packs")>();
  return { ...actual, importCheckPack: vi.fn(), importPack: vi.fn() };
});

const _PACK: FactorPack = {
  pack_slug: "user/test",
  version: "1.0.0",
  factors: [],
  citation: { title: "Test Pack" },
};

function _setup(onImport: (p: FactorPack, url?: string | null) => void = vi.fn()): void {
  renderWithIntl(<PackIO pack={_PACK} onImport={onImport} />);
}

function _fetchUrl(url: string): void {
  fireEvent.change(screen.getByLabelText("pack URL 입력"), {
    target: { value: url },
  });
  fireEvent.click(screen.getByRole("button", { name: "URL 가져오기" }));
}

describe("PackIO — client-fetch URL import (ADR-0032 D2)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("https 아닌 URL → scheme 오류 (fetch 호출 0)", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    _setup();
    _fetchUrl("http://example.com/pack.json");
    expect(await screen.findByText("https URL 만 지원합니다.")).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();  // 가드가 fetch 전에 차단.
  });

  it("성공 → 가져온 JSON 이 import textarea 에 채워짐", async () => {
    const body = '{"type":"factor-pack","pack_slug":"community/x","factors":[]}';
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true, status: 200, text: () => Promise.resolve(body),
      }),
    );
    _setup();
    _fetchUrl("https://raw.githubusercontent.com/u/r/main/pack.json");
    // 사용자 검토용으로 textarea 에 채워짐(자동 import 아님).
    expect(await screen.findByDisplayValue(body)).toBeInTheDocument();
  });

  it("fetch 응답 not-ok → 가져오기 실패 오류", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false, status: 404, text: () => Promise.resolve(""),
      }),
    );
    _setup();
    _fetchUrl("https://example.com/missing.json");
    expect(await screen.findByText(/가져오기 실패/)).toBeInTheDocument();
  });

  it("비-JSON 응답 → JSON 형식 오류", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true, status: 200, text: () => Promise.resolve("<html>not json"),
      }),
    );
    _setup();
    _fetchUrl("https://example.com/page.html");
    expect(await screen.findByText("JSON 형식이 아닙니다.")).toBeInTheDocument();
  });

  it("과대 응답(>1MB) → 크기 오류", async () => {
    const huge = '{"x":"' + "a".repeat(1_000_001) + '"}';
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true, status: 200, text: () => Promise.resolve(huge),
      }),
    );
    _setup();
    _fetchUrl("https://example.com/huge.json");
    expect(await screen.findByText(/너무 큽니다/)).toBeInTheDocument();
  });

  it("네트워크/CORS 오류 → 로컬 파일 안내", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("CORS")));
    _setup();
    _fetchUrl("https://no-cors.example.com/pack.json");
    expect(await screen.findByText(/가져올 수 없습니다/)).toBeInTheDocument();
  });
});

describe("PackIO — provenance 전달 (ADR-0032 D3)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.mocked(importCheckPack).mockReset();
    vi.mocked(importPack).mockReset();
  });

  it("URL fetch 후 적용 → onImport 에 출처 URL 을 함께 전달", async () => {
    const url = "https://raw.githubusercontent.com/u/r/main/pack.json";
    const body = '{"type":"factor-pack","pack_slug":"community/x","factors":[]}';
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true, status: 200, text: () => Promise.resolve(body),
      }),
    );
    // import-check: valid + 충돌 0 → 자동 Pass2.
    vi.mocked(importCheckPack).mockResolvedValue({
      valid: true, issues: [], conflicts: [], clean: [], hashMismatch: false,
    });
    vi.mocked(importPack).mockResolvedValue({
      valid: true, issues: [],
      applied: [],
      pack: {
        pack_slug: "community/x", version: "1.0.0", factors: [],
        citation: { title: "x" }, content_hash: "sha256:" + "a".repeat(64),
      },
    });

    const onImport = vi.fn();
    _setup(onImport);
    _fetchUrl(url);
    // fetch 반영(textarea) 대기 후 "적용".
    await screen.findByDisplayValue(body);
    fireEvent.click(screen.getByRole("button", { name: "반영" }));

    await waitFor(() => {
      // 2번째 인자 = provenance(fetch 한 출처 URL).
      expect(onImport).toHaveBeenCalledWith(
        expect.objectContaining({ pack_slug: "community/x" }),
        url,
      );
    });
    // content_hash 는 editor 반영 시 제거(봉인 필드 제외).
    expect(onImport.mock.calls[0]?.[0]).not.toHaveProperty("content_hash");
  });

  it("수동 입력(paste) 후 적용 → onImport 의 출처는 null", async () => {
    const body = '{"type":"factor-pack","pack_slug":"user/y","factors":[]}';
    vi.mocked(importCheckPack).mockResolvedValue({
      valid: true, issues: [], conflicts: [], clean: [], hashMismatch: false,
    });
    vi.mocked(importPack).mockResolvedValue({
      valid: true, issues: [],
      applied: [],
      pack: {
        pack_slug: "user/y", version: "1.0.0", factors: [],
        citation: { title: "y" }, content_hash: "sha256:" + "b".repeat(64),
      },
    });

    const onImport = vi.fn();
    _setup(onImport);
    // textarea 에 직접 입력(URL fetch 아님) → provenance 없음.
    fireEvent.change(screen.getByLabelText("pack JSON 가져오기 입력"), {
      target: { value: body },
    });
    fireEvent.click(screen.getByRole("button", { name: "반영" }));

    await waitFor(() => {
      expect(onImport).toHaveBeenCalledWith(
        expect.objectContaining({ pack_slug: "user/y" }),
        null,
      );
    });
  });
});

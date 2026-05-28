/**
 * Backend API client — fetch wrapper.
 *
 * 환경변수:
 *   - `NEXT_PUBLIC_API_BASE_URL` — backend FastAPI base URL.
 *     운영 default `http://localhost:8000` (dev). 운영 host 는 빌드 시 주입.
 *
 * 책임:
 *   - Base URL 일관 prepend.
 *   - JSON Content-Type + Accept header 자동.
 *   - As-of query param 자동 추가 (호출자가 명시 전달).
 *   - HTTP error → `ApiError` 정규화 (status + message + raw response).
 *   - JSON parse 실패 → ApiError.
 *
 * 단방향 — 호출자가 RequestInit 의 method/body 직접 control. 본 module 은
 * URL composition + error normalization 만.
 */

const DEFAULT_API_BASE_URL = "http://localhost:8000";

/**
 * API base URL 결정 — 빌드 시 `NEXT_PUBLIC_API_BASE_URL` 주입 우선.
 * env var 미설정 시 dev default. process.env 는 빌드 시 inline 대체.
 */
function resolveApiBaseUrl(): string {
  // Next.js 의 NEXT_PUBLIC_* 은 빌드 시점에 inline. runtime 변경 X.
  const fromEnv = process.env["NEXT_PUBLIC_API_BASE_URL"];
  return fromEnv && fromEnv.length > 0 ? fromEnv : DEFAULT_API_BASE_URL;
}

export class ApiError extends Error {
  readonly status: number;
  readonly body: string;

  constructor(status: number, message: string, body: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

interface FetchJsonOptions {
  readonly method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  readonly body?: unknown;
  /** Query string parameter. 값은 string 으로 coerce 후 URLSearchParams. */
  readonly searchParams?: Readonly<Record<string, string>>;
  /** signal — AbortController 등. TanStack Query 가 자동 주입. */
  readonly signal?: AbortSignal;
}

/**
 * Backend API 호출 + JSON parse.
 *
 * @param path API path (leading `/` 포함, `/api/screen` 등)
 * @param options method / body / searchParams / signal
 * @returns parsed JSON
 * @throws ApiError on non-2xx response or JSON parse failure.
 */
export async function fetchJson<T>(
  path: string,
  options: FetchJsonOptions = {},
): Promise<T> {
  const baseUrl = resolveApiBaseUrl();
  const url = new URL(path, baseUrl);
  if (options.searchParams) {
    for (const [key, value] of Object.entries(options.searchParams)) {
      url.searchParams.set(key, value);
    }
  }

  const init: RequestInit = {
    method: options.method ?? "GET",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    // T31 NextAuth 합류 전 선제 — backend 가 httpOnly cookie 기반 session 을
    // 사용하므로 cross-origin (NEXT_PUBLIC_API_BASE_URL≠Next.js host) 에서도
    // cookie 가 전송되어야 함. 미설정 시 T31 합류 후 silent 401 회귀 (oracle
    // T39 M1).
    credentials: "include",
    signal: options.signal,
  };
  if (options.body !== undefined) {
    init.body = JSON.stringify(options.body);
  }

  const response = await fetch(url.toString(), init);
  const rawText = await response.text();

  if (!response.ok) {
    throw new ApiError(
      response.status,
      `API ${response.status} ${response.statusText}`,
      rawText,
    );
  }

  if (!rawText) {
    // 빈 body — JSON parse 가 throw. `T` 가 void 인 경우만 caller 가 cast.
    return undefined as unknown as T;
  }

  try {
    return JSON.parse(rawText) as T;
  } catch (cause) {
    throw new ApiError(
      response.status,
      `API JSON parse failed: ${(cause as Error).message}`,
      rawText,
    );
  }
}

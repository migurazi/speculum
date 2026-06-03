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
 *   - Authorization Bearer 헤더 주입 — setAuthToken 으로 주입된 HS256 JWS.
 *   - HTTP error → `ApiError` 정규화 (status + message + raw response).
 *   - JSON parse 실패 → ApiError.
 *
 * 인증 모델 B (ADR-0021 D1.1 / T68):
 *   NextAuth JWE 세션이 아닌 별도 발급된 HS256 JWS 를 Authorization Bearer 로
 *   전달. setAuthToken 으로 주입, 미인증(빈 token) 시 헤더 미전송 → backend 401.
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

/**
 * 현재 HS256 JWS 액세스 토큰.
 *
 * module-level 변수 — SessionProvider 하위의 AuthTokenSync 컴포넌트가
 * useSession 에서 꺼낸 accessToken 을 setAuthToken 으로 주입한다.
 * 미로그인 또는 세션 만료 시 빈 문자열 → Authorization 헤더 미전송.
 */
let _authToken = "";

/**
 * fetchJson 에서 사용할 Bearer 토큰 설정.
 *
 * 호출 주체: AuthTokenSync (providers.tsx 하위 컴포넌트).
 * 토큰이 빈 문자열이면 이전 토큰이 초기화됨(로그아웃).
 */
export function setAuthToken(token: string): void {
  _authToken = token;
}

interface FetchJsonOptions {
  readonly method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  readonly body?: unknown;
  /** Query string parameter. 값은 string 으로 coerce 후 URLSearchParams. */
  readonly searchParams?: Readonly<Record<string, string>>;
  /** signal — AbortController 등. TanStack Query 가 자동 주입. */
  readonly signal?: AbortSignal;
  /**
   * 호출별 override 토큰. 미전달 시 module-level _authToken 사용.
   * SSR 서버 컴포넌트 등 setAuthToken 미사용 경로에서 직접 주입 가능.
   */
  readonly accessToken?: string;
}

/**
 * Backend API 호출 + JSON parse.
 *
 * @param path API path (leading `/` 포함, `/api/screen` 등)
 * @param options method / body / searchParams / signal / accessToken
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

  // Authorization Bearer — 호출별 override 우선, 없으면 module-level.
  const token = options.accessToken ?? _authToken;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const init: RequestInit = {
    method: options.method ?? "GET",
    headers,
    // 모델 B (ADR-0021 D1.1): Bearer 토큰 전송으로 전환.
    // cross-origin cookie 의존 제거 — credentials "same-origin" 으로 완화.
    credentials: "same-origin",
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

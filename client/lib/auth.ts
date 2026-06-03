/**
 * Speculum NextAuth config — T68 Google OAuth 골격 (ADR-0021 D1.1).
 *
 * 모델 B: NextAuth 기본 JWE 세션 토큰은 backend 가 복호화 불가 →
 * `callbacks.jwt` 에서 별도 HS256 JWS 액세스 토큰을 `AUTH_SECRET` 으로 발급.
 * backend 는 동일 `AUTH_SECRET` 으로 `Authorization: Bearer <JWS>` 를 검증.
 *
 * JWT 서명 구현: Web Crypto API (crypto.subtle) — 추가 의존 없음.
 * Node.js 18+ / Next.js Edge runtime 모두 지원.
 *
 * 환경변수 (실값은 Google credential 확보 시 주입 — .env.example 참조):
 *   - AUTH_SECRET       — HS256 서명 키 (backend 와 동일 공유 secret)
 *   - AUTH_GOOGLE_ID    — Google OAuth 2.0 Client ID
 *   - AUTH_GOOGLE_SECRET — Google OAuth 2.0 Client Secret
 *   - AUTH_URL          — Next.js 앱 host (운영 배포 시 명시)
 *
 * 관련 ADR / 문서:
 * - ADR-0021 D1.1 (2026-06-02) — Google OAuth 인증 설계 확정
 * - M2_PLAN T68 — NextAuth Google OAuth 골격
 */

import NextAuth from "next-auth";
import GoogleProvider from "next-auth/providers/google";

// ── HS256 JWS 서명 (Web Crypto API — 추가 의존 없음) ────────────────────────

/**
 * Base64URL 인코딩 (RFC 7515 §2).
 * Buffer/Uint8Array 모두 처리, btoa 대신 직접 변환으로 Edge runtime 안전.
 */
function base64url(input: Uint8Array | string): string {
  const bytes =
    typeof input === "string" ? new TextEncoder().encode(input) : input;
  // Buffer → binary string → btoa → base64url
  let binary = "";
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]!);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * HS256 JWS 액세스 토큰 발급.
 *
 * backend `PyJWT HS256` 검증 계약:
 *   claims: sub (google sub), email
 *   algorithm: HS256
 *   expiresIn: 1h
 */
async function issueAccessToken(sub: string, email: string): Promise<string> {
  const secret = process.env["AUTH_SECRET"] ?? "";
  const now = Math.floor(Date.now() / 1000);

  const header = base64url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const payload = base64url(
    JSON.stringify({ sub, email, iat: now, exp: now + 3600 }),
  );
  const signingInput = `${header}.${payload}`;

  const keyMaterial = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signatureBuffer = await crypto.subtle.sign(
    "HMAC",
    keyMaterial,
    new TextEncoder().encode(signingInput),
  );
  const signature = base64url(new Uint8Array(signatureBuffer));

  return `${signingInput}.${signature}`;
}

// ── NextAuth config ──────────────────────────────────────────────────────────

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [
    GoogleProvider({
      clientId: process.env["AUTH_GOOGLE_ID"],
      clientSecret: process.env["AUTH_GOOGLE_SECRET"],
    }),
  ],

  session: { strategy: "jwt" },

  callbacks: {
    /**
     * jwt callback — Google 로그인 시 account/profile 에서 sub·email 추출,
     * HS256 JWS 액세스 토큰 발급 후 token 에 보관.
     *
     * account 가 있을 때(최초 로그인)만 갱신 — 이후 session 재조회 시에는
     * token 에 이미 있는 accessToken 을 그대로 전달.
     */
    async jwt({ token, account, profile }) {
      if (account && profile) {
        // Google sub 은 profile.sub (표준 OIDC) 또는 account.providerAccountId.
        const sub =
          (profile as { sub?: string }).sub ?? account.providerAccountId;
        const email = (profile.email as string | undefined) ?? "";
        token["sub"] = sub;
        token["email"] = email;
        token["accessToken"] = await issueAccessToken(sub, email);
      }
      return token;
    },

    /**
     * session callback — client 에서 useSession() 으로 accessToken 참조 가능.
     */
    session({ session, token }) {
      const s = session as unknown as Record<string, unknown>;
      if (token["accessToken"]) {
        s["accessToken"] = token["accessToken"];
      }
      if (token["email"]) {
        session.user = {
          ...session.user,
          email: token["email"] as string,
        };
      }
      return session;
    },
  },
});

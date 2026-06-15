/**
 * NextAuth v5 모듈 타입 확장 — Session / JWT 에 accessToken 추가 (ADR-0021 D1.1
 * 모델 B).
 *
 * callbacks.session(lib/auth.ts) 이 HS256 JWS accessToken 을 Session 에 실어
 * 보내고, AuthTokenSync(components/AuthTokenSync.tsx) 가 이를 읽어 fetchJson 의
 * Bearer 헤더에 주입한다. 본 augmentation 이 없으면 그 동적 필드 접근에
 * `as unknown as Record<string, unknown>` 우회 캐스트가 필요했다 — 타입 안전성을
 * 잃고 오타·형변경을 컴파일 단계에서 못 잡는다.
 *
 * augmentation 규칙: 파일이 module 이어야(아래 side-effect import) 기존 interface
 * 를 **확장**한다. import 없는 ambient .d.ts 는 `declare module` 이 교체가 되어
 * NextAuth 기본 필드를 잃는다. tsconfig `include` 의 lib 디렉터리 glob 에 포함됨.
 */

import "next-auth";
import "next-auth/jwt";

declare module "next-auth" {
  interface Session {
    /** backend 검증용 HS256 JWS 액세스 토큰 (모델 B). 미로그인 시 부재. */
    accessToken?: string;
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    /** jwt callback 이 발급한 HS256 JWS — session callback 이 Session 으로 전달. */
    accessToken?: string;
  }
}

import type { Config } from "tailwindcss";

/**
 * Speculum Tailwind config — T31 scaffold.
 *
 * shadcn/ui (M0 합류) 와 정합. 현재는 minimum base — shadcn CLI 가 후속 cycle
 * 에서 색상 token / 라디우스 / 폰트 등을 추가.
 */
const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        // 한글 — Pretendard 권장 (운영 시 next/font 로 self-host).
        sans: [
          "Pretendard",
          "-apple-system",
          "BlinkMacSystemFont",
          "system-ui",
          "Roboto",
          "sans-serif",
        ],
      },
    },
  },
  plugins: [],
};

export default config;

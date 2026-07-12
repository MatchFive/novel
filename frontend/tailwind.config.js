/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      borderRadius: {
        none: "0",
        sm: "6px",
        DEFAULT: "8px",
        lg: "12px",
      },
      colors: {
        ink: "#3a2c22",
        "ink-soft": "#6b5d4f",
        paper: "#faf6ee",
        surface: "#fffdf8",
        "surface-2": "#f3ecdf",
        muted: "#9a8c7b",
        line: "#e6dccb",
        accent: "#b07a3c",
        "accent-strong": "#8f5f2c",
        "accent-soft": "#f0e4d2",
      },
      fontFamily: {
        sans: ['"Inter"', '"PingFang SC"', '"Microsoft YaHei"', "system-ui", "sans-serif"],
        serif: ['"Songti SC"', '"STSong"', '"SimSun"', '"Noto Serif SC"', "serif"],
      },
      boxShadow: {
        soft: "0 1px 3px rgba(58,44,34,.08), 0 6px 18px rgba(58,44,34,.06)",
        "card-hover": "0 2px 6px rgba(58,44,34,.10), 0 12px 28px rgba(58,44,34,.08)",
      },
    },
  },
  plugins: [],
};

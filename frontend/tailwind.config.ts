import type { Config } from "tailwindcss"

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        void:    "#09090B",
        surface: "#18181A",
        border:  "rgba(255,255,255,0.07)",
        accent:  "#6366F1",
        cyan:    "#06B6D4",
        primary: "#FAFAFA",
        muted:   "#A1A1AA",
        dim:     "#52525B",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
      },
      backgroundImage: {
        "accent-gradient": "linear-gradient(135deg, #6366F1 0%, #06B6D4 100%)",
        "glow-radial":     "radial-gradient(ellipse at 50% 0%, rgba(99,102,241,0.15) 0%, transparent 70%)",
      },
      boxShadow: {
        "accent-glow": "0 0 0 1px #6366F1, 0 0 20px rgba(99,102,241,0.3)",
        "surface":     "0 1px 3px rgba(0,0,0,0.4), 0 1px 2px rgba(0,0,0,0.3)",
        "card":        "0 4px 24px rgba(0,0,0,0.5)",
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to:   { opacity: "1", transform: "translateY(0)" },
        },
        "pulse-glow": {
          "0%, 100%": { boxShadow: "0 0 8px rgba(99,102,241,0.4)" },
          "50%":       { boxShadow: "0 0 20px rgba(99,102,241,0.7)" },
        },
      },
      animation: {
        "fade-in":    "fade-in 0.2s ease-out",
        "pulse-glow": "pulse-glow 2s ease-in-out infinite",
      },
    },
  },
  plugins: [],
}

export default config

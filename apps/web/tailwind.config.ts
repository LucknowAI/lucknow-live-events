import type { Config } from "tailwindcss";

// In Tailwind v4, design tokens are declared in CSS via @theme.
// This file is kept for IDE type-checking only.
const config: Config = {
  content: [
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
};
export default config;

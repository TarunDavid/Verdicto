/** Tailwind config for the local CSS build (replaces the CDN in production).
 *  Rebuild after changing templates:  python manage.py buildcss
 *
 *  Look: an editorial fact-check desk. Ink-black paper, ivory type, hairline rules, a serif display
 *  face, monospace for case numbers / weights / dates, verdicts as rubber stamps. The grey, teal and
 *  sky scales are remapped to warm tones so every page shares the palette. */
module.exports = {
  content: ["./templates/**/*.html", "./**/forms.py", "./**/views.py", "./**/templatetags/*.py"],
  safelist: ["stamp-lg", "stamp-xl"],   // built as stamp-{{ size }} in _badge.html
  theme: {
    extend: {
      colors: {
        accent: "#E0383E",                       // signal red (SPEC)
        ink: { DEFAULT: "#0e0d0b", 2: "#151411", 3: "#1c1a16", 4: "#24211c" },
        paper: { DEFAULT: "#efe9dd", dim: "#d9d2c4" },
        rule: { DEFAULT: "#2b2823", strong: "#3d3931" },
        // warm greys instead of Tailwind's cool zinc
        zinc: { 50: "#f7f4ee", 100: "#efe9dd", 200: "#ddd6c8", 300: "#c4bcac", 400: "#a39b8b",
                500: "#7d766a", 600: "#5c564d", 700: "#3f3a33", 800: "#2a2621", 900: "#1a1814", 950: "#0e0d0b" },
        // "teal" highlights become brass
        teal: { 100: "#f6ecd2", 200: "#eedcaa", 300: "#e3c67c", 400: "#d4ac55", 500: "#b88f3a", 600: "#8f6c2a" },
        // "sky" (verified badge, under investigation) becomes ballpoint blue
        sky: { 200: "#cfdcf0", 300: "#a9c1e6", 400: "#86a6db", 500: "#6487c4", 700: "#3a5384" },
      },
      fontFamily: {
        sans: ["Inter", "Noto Sans Kannada", "ui-sans-serif", "system-ui"],
        display: ["Fraunces", "Noto Serif Kannada", "Georgia", "serif"],
        mono: ["JetBrains Mono", "Noto Sans Kannada", "ui-monospace", "monospace"],
      },
      borderRadius: { xl: "7px", "2xl": "10px" },
    },
  },
};

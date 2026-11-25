/** Tailwind config for the local CSS build (replaces the CDN in production).
 *  Rebuild after changing templates:  ./tools/build_css.sh   (or: python manage.py buildcss) */
module.exports = {
  content: ["./templates/**/*.html", "./**/forms.py", "./**/views.py"],
  theme: {
    extend: {
      colors: { accent: "#E0383E" },
      fontFamily: { sans: ["Inter", "Noto Sans Kannada", "ui-sans-serif", "system-ui"] },
    },
  },
};

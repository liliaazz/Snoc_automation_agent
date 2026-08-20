/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      fontFamily: {
        outfit: ["Outfit", "sans-serif"],
        oxanium: ["Oxanium", "sans-serif"],
      },
      colors: {
        "esi-orange": "#EA8B00",
      },
      boxShadow: {
        "2xl": "0 12px 32px -8px rgba(15, 15, 15, 0.14)",
      },
    },
  },
  plugins: [],
};

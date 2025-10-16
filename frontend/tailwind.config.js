/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
    "./node_modules/@tremor/react/dist/tremor.min.css",
  ],
  theme: {
    extend: {},
  },
  plugins: [],
  darkMode: ["class"],  // Enables dark via <html class="dark">
}
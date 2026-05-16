// Dark/Light theme toggle
import { $ } from '../core/dom.js';

const THEME_STORAGE_KEY = "artemis-theme";

function readStoredTheme() {
  try {
    return localStorage.getItem(THEME_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredTheme(theme) {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Ignore unavailable or blocked storage during startup.
  }
}

export function applyTheme(theme) {
  if (typeof document === "undefined") return;

  document.documentElement.setAttribute("data-theme", theme);
  writeStoredTheme(theme);

  // Icon visibility is handled by the approved design's CSS:
  //   .rail-theme-toggle.is-light .rail-theme-glyph-sun  { opacity: 1 }
  //   .rail-theme-toggle.is-light .rail-theme-glyph-moon { opacity: 0 }
  //   .rail-theme-toggle.is-dark  .rail-theme-glyph-sun  { opacity: 0 }
  //   .rail-theme-toggle.is-dark  .rail-theme-glyph-moon { opacity: 1 }
  // The `.is-light`/`.is-dark` class on #theme-toggle-btn is mirrored from
  // the root [data-theme] by artemis-shell.js#initThemeButtonClassSync.
  // Both icons use grid-area:1/1 so they stack; only opacity/transform
  // cross-fade between them. Legacy inline display:none/block from older
  // theme.js versions was overriding that cross-fade — clear it here so
  // pre-existing inline styles don't stick.
  if ($.themeIconSun) $.themeIconSun.style.display = "";
  if ($.themeIconMoon) $.themeIconMoon.style.display = "";

  // Update Mermaid theme
  if (typeof mermaid !== "undefined") {
    mermaid.initialize({ startOnLoad: false, theme: theme === "light" ? "default" : "dark" });
  }

  // Update highlight.js theme stylesheet
  const hljsLink = document.getElementById("hljs-theme");
  if (hljsLink) {
    hljsLink.href = theme === "light"
      ? "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css"
      : "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css";
  }
}

// Initialize theme from localStorage (default to light theme)
const savedTheme = readStoredTheme() || "light";
applyTheme(savedTheme);

if ($.themeToggleBtn) {
  $.themeToggleBtn.addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme") || "light";
    applyTheme(current === "dark" ? "light" : "dark");
  });
} else {
  console.warn("Theme toggle button not found in DOM");
}

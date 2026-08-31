/* Applied before first paint so the page never flashes the wrong theme. */
(function () {
  try {
    var stored = localStorage.getItem("jobhunt-theme");
    if (stored === "light" || stored === "dark") {
      document.documentElement.setAttribute("data-theme", stored);
    }
  } catch (err) { /* storage unavailable — fall back to prefers-color-scheme */ }
})();

/* JobHunt — interface behaviour.
   Everything here is progressive: the app works without it, just with more
   page reloads. Four jobs: CSRF for htmx, toasts, the theme switch, and
   drag-and-drop on the pipeline board. */

(function () {
  "use strict";

  // --- CSRF ---------------------------------------------------------------
  const token = document.querySelector('meta[name="csrf-token"]');
  if (token) {
    document.body.addEventListener("htmx:configRequest", function (event) {
      event.detail.headers["X-CSRFToken"] = token.content;
    });
  }

  // --- Toasts -------------------------------------------------------------
  const TONES = { success: "t-green", info: "t-blue", warning: "t-amber", error: "t-red" };

  function showToast(message, tone) {
    const host = document.getElementById("toasts");
    if (!host || !message) return;
    const el = document.createElement("div");
    el.className = "toast " + (TONES[tone] || TONES.success);
    el.setAttribute("role", "status");
    el.textContent = message;
    host.appendChild(el);
    window.setTimeout(function () {
      el.classList.add("is-leaving");
      window.setTimeout(function () { el.remove(); }, 220);
    }, 3200);
  }

  document.body.addEventListener("toast", function (event) {
    const data = event.detail || {};
    showToast(data.message, data.tone);
  });
  window.jobhuntToast = showToast;

  // --- Theme --------------------------------------------------------------
  const STORAGE_KEY = "jobhunt-theme";

  function applyTheme(value) {
    if (value === "light" || value === "dark") {
      document.documentElement.setAttribute("data-theme", value);
    } else {
      document.documentElement.removeAttribute("data-theme");
    }
    document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
      const dark = resolvedTheme() === "dark";
      button.setAttribute("aria-label", dark ? "Passer en clair" : "Passer en sombre");
      button.querySelectorAll("[data-theme-icon]").forEach(function (icon) {
        icon.hidden = icon.dataset.themeIcon !== (dark ? "sun" : "moon");
      });
    });
  }

  function resolvedTheme() {
    const stored = document.documentElement.getAttribute("data-theme");
    if (stored) return stored;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  document.addEventListener("click", function (event) {
    const button = event.target.closest("[data-theme-toggle]");
    if (!button) return;
    const next = resolvedTheme() === "dark" ? "light" : "dark";
    try { localStorage.setItem(STORAGE_KEY, next); } catch (err) { /* private mode */ }
    applyTheme(next);
  });

  applyTheme(document.documentElement.getAttribute("data-theme"));

  // --- Pipeline drag & drop ----------------------------------------------
  // Cards carry their own endpoint; columns carry the target status. The
  // whole board comes back from the server so the counters stay honest.
  let dragged = null;

  document.addEventListener("dragstart", function (event) {
    const card = event.target.closest("[data-drag-card]");
    if (!card) return;
    dragged = card;
    card.classList.add("is-dragging");
    event.dataTransfer.effectAllowed = "move";
    try { event.dataTransfer.setData("text/plain", card.dataset.appId || ""); } catch (err) { /* Safari */ }
  });

  document.addEventListener("dragend", function () {
    if (dragged) dragged.classList.remove("is-dragging");
    document.querySelectorAll(".board__col.is-over").forEach(function (col) {
      col.classList.remove("is-over");
    });
    dragged = null;
  });

  document.addEventListener("dragover", function (event) {
    const column = event.target.closest("[data-drop-status]");
    if (!column || !dragged) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    column.classList.add("is-over");
  });

  document.addEventListener("dragleave", function (event) {
    const column = event.target.closest("[data-drop-status]");
    if (column && !column.contains(event.relatedTarget)) column.classList.remove("is-over");
  });

  document.addEventListener("drop", function (event) {
    const column = event.target.closest("[data-drop-status]");
    if (!column || !dragged) return;
    event.preventDefault();
    column.classList.remove("is-over");

    const status = column.dataset.dropStatus;
    const url = dragged.dataset.statusUrl;
    if (!url || !status || dragged.dataset.status === status) return;

    window.htmx.ajax("POST", url, {
      target: "#board",
      swap: "outerHTML",
      values: { status: status, source: "board" },
    });
  });

  // --- Quick-add dialog ---------------------------------------------------
  document.addEventListener("click", function (event) {
    const opener = event.target.closest("[data-dialog-open]");
    if (opener) {
      const dialog = document.getElementById(opener.dataset.dialogOpen);
      if (dialog && !dialog.open) {
        dialog.showModal();
        // The dialog's body loads on this event, so it costs nothing until opened.
        document.body.dispatchEvent(new CustomEvent(opener.dataset.dialogOpen + "-open"));
      }
      return;
    }
    const closer = event.target.closest("[data-dialog-close]");
    if (closer) {
      const dialog = closer.closest("dialog");
      if (dialog) dialog.close();
    }
  });

  // Click outside the panel closes the dialog.
  document.addEventListener("click", function (event) {
    if (event.target.tagName !== "DIALOG") return;
    const box = event.target.getBoundingClientRect();
    const outside =
      event.clientY < box.top || event.clientY > box.bottom ||
      event.clientX < box.left || event.clientX > box.right;
    if (outside) event.target.close();
  });

  document.body.addEventListener("close-dialog", function () {
    document.querySelectorAll("dialog[open]").forEach(function (d) { d.close(); });
  });

  // --- Confirmations ------------------------------------------------------
  document.body.addEventListener("htmx:confirm", function (event) {
    const message = event.detail.elt.getAttribute("data-confirm");
    if (!message) return;
    event.preventDefault();
    if (window.confirm(message)) event.detail.issueRequest(true);
  });

  // --- Keyboard shortcuts -------------------------------------------------
  document.addEventListener("keydown", function (event) {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    if (typing || event.metaKey || event.ctrlKey || event.altKey) return;

    if (event.key === "/") {
      const search = document.querySelector("[data-search-input]");
      if (search) { event.preventDefault(); search.focus(); search.select(); }
    }
    if (event.key === "n") {
      const dialog = document.getElementById("quick-add");
      if (dialog && !dialog.open) {
        event.preventDefault();
        dialog.showModal();
        document.body.dispatchEvent(new CustomEvent("quick-add-open"));
      }
    }
  });
})();

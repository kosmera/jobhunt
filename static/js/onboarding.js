/* JobHunt — the « Bienvenue » onboarding flow.
   Everything here is progressive: without this file every step is a plain
   form with an enabled Continue button, native radios and checkboxes, a
   comma-separated text field instead of chips, a plain file input and a
   native number input. Loaded with `defer` after app.js and fields.js by
   layout.html.

   Data-attribute contract (element → behaviour):

   form[data-requires-choice]
       The primary button (`.onb__actions .btn--primary`) is disabled until a
       radio or checkbox is checked, or a file input holds a file.
   form[data-auto-advance]
       A `change` on a radio that follows a pointerdown (last 500 ms) submits
       the form after 120 ms — 0 ms under prefers-reduced-motion. A change made
       from the keyboard never submits.
   button[data-skip-label="Passer"]
       The primary button of a skippable choice form. While nothing is checked
       it reads the skip label and posts `action=skip`; once something is
       checked it gets its own label back. The form's ghost skip button
       (`button[name=action][value=skip]`) is hidden, the no-JS path keeps it.
   button[type=button][data-reveal]
       Un-hides every `hidden` sibling, focuses the first, hides itself. (The
       sectors screen uses a native <details> instead, which needs no script.)
   label[data-dropzone] (wrapping input[type=file])
       Drag-over highlight (`.is-over`), drop assigns the file, the name is
       echoed in `.dropzone__file` (« Fichier prêt : … », created if absent).
   form[data-upload-form]
       Submitting with a file sends the FormData by XMLHttpRequest, fills
       `.upload-bar` / `.upload-bar__fill` (created if absent) with the upload
       progress. The request carries `X-Requested-With: XMLHttpRequest`; the
       view answers a redirect with `204` + `X-Onboarding-Redirect`, which is
       followed by navigating there (so the messages of that POST survive).
       Once the upload finishes, an indeterminate bar shows preparation until
       navigation. Validation errors replace the form; uncertain network or
       server failures offer a GET recovery link without resubmitting the CV.
       A skip submit is left alone unless an upload is already in progress.
   [data-cv-analysis][data-status-url][data-document-id][data-status]
       Polls the HTML status endpoint every 2 seconds while pending/running.
       Replaces only a matching document's analysis section. Terminal states
       stop polling; unavailable progress offers a link to refresh the page.

   The chips combobox, the check-all / exclusive boxes, the salary period
   toggle and the focus on the first invalid field live in fields.js, loaded
   just before this file: their state is already in sync when the handlers
   below read it. */

(function () {
  "use strict";

  var REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)");
  var POINTER_WINDOW_MS = 500;
  var AUTO_ADVANCE_MS = 120;
  var lastPointerDown = -Infinity;

  document.addEventListener("pointerdown", function () { lastPointerDown = performance.now(); }, true);

  function primaryOf(form) {
    return form.querySelector(".onb__actions .btn--primary") || form.querySelector(".btn--primary");
  }

  function hasChoice(form) {
    if (form.querySelector('input[type="radio"]:checked, input[type="checkbox"]:checked')) return true;
    return Array.prototype.some.call(form.querySelectorAll('input[type="file"]'), function (input) {
      return input.files && input.files.length > 0;
    });
  }

  function submitForm(form) {
    if (form.dataset.submitting) return;
    form.dataset.submitting = "1";
    if (form.requestSubmit) form.requestSubmit(); else form.submit();
  }
  // A page restored from the back-forward cache must be submittable again.
  window.addEventListener("pageshow", function () {
    document.querySelectorAll("form[data-submitting]").forEach(function (form) { delete form.dataset.submitting; });
  });

  // --- 1. Choice gate ------------------------------------------------------
  function updateGate(form) {
    if (!form.hasAttribute("data-requires-choice")) return;
    var button = primaryOf(form);
    if (button) button.disabled = !!form.dataset.uploading || !hasChoice(form);
  }

  // --- 2. « Passer » ↔ « Continuer » ---------------------------------------
  var originalLabels = new WeakMap();

  function syncSkipLabel(form) {
    var button = form.querySelector("[data-skip-label]");
    if (!button) return;
    if (!originalLabels.has(button)) {
      originalLabels.set(button, { text: button.textContent, name: button.getAttribute("name"), value: button.getAttribute("value") });
      var ghost = form.querySelector('button[name="action"][value="skip"]:not([data-skip-label])');
      if (ghost) ghost.hidden = true;
    }
    var original = originalLabels.get(button);
    if (hasChoice(form)) {
      button.textContent = original.text;
      if (original.name === null) button.removeAttribute("name"); else button.setAttribute("name", original.name);
      if (original.value === null) button.removeAttribute("value"); else button.setAttribute("value", original.value);
    } else {
      button.textContent = button.dataset.skipLabel;
      button.setAttribute("name", "action");
      button.setAttribute("value", "skip");
    }
  }

  document.addEventListener("change", function (event) {
    var input = event.target;
    var form = input.form;
    if (!form || !form.closest(".onb")) return;
    updateGate(form);
    syncSkipLabel(form);
    // 2. Auto-advance: pointer-originated only, so arrow keys on the radios never submit.
    if (form.hasAttribute("data-auto-advance") && input.type === "radio" && input.checked
        && performance.now() - lastPointerDown < POINTER_WINDOW_MS) {
      window.setTimeout(function () { submitForm(form); }, REDUCED_MOTION.matches ? 0 : AUTO_ADVANCE_MS);
    }
  });

  // --- 3. « Voir plus » ----------------------------------------------------
  document.addEventListener("click", function (event) {
    var trigger = event.target.closest("[data-reveal]");
    if (!trigger) return;
    var first = null;
    Array.prototype.forEach.call(trigger.parentElement.children, function (child) {
      if (child === trigger || !child.hidden) return;
      child.hidden = false;
      if (!first) first = child.querySelector("input, button, a") || child;
    });
    trigger.hidden = true;
    if (first && first.focus) first.focus();
  });

  // --- 4. Dropzone and upload progress ------------------------------------
  function echoFile(input) {
    var zone = input.closest("[data-dropzone]");
    if (!zone) return;
    var echo = zone.querySelector(".dropzone__file");
    if (!echo) {
      echo = document.createElement("span");
      echo.className = "dropzone__file";
      echo.setAttribute("aria-live", "polite");
      zone.appendChild(echo);
    }
    var file = input.files && input.files[0];
    echo.textContent = file ? "Fichier prêt : " + file.name : "";
    echo.hidden = !file;
  }

  document.addEventListener("change", function (event) {
    if (event.target.type === "file") echoFile(event.target);
  });

  var hasDropzone = document.querySelector("[data-dropzone]") !== null;

  document.addEventListener("dragover", function (event) {
    if (!hasDropzone) return;
    event.preventDefault();       // the browser must not open a file dropped beside the zone
    var zone = event.target.closest("[data-dropzone]");
    if (zone) { event.dataTransfer.dropEffect = "copy"; zone.classList.add("is-over"); }
  });
  document.addEventListener("dragenter", function (event) {
    var zone = event.target.closest("[data-dropzone]");
    if (zone) zone.classList.add("is-over");
  });
  document.addEventListener("dragleave", function (event) {
    var zone = event.target.closest("[data-dropzone]");
    if (zone && !zone.contains(event.relatedTarget)) zone.classList.remove("is-over");
  });
  document.addEventListener("drop", function (event) {
    if (!hasDropzone) return;
    event.preventDefault();
    var zone = event.target.closest("[data-dropzone]");
    if (!zone) return;
    zone.classList.remove("is-over");
    var input = zone.querySelector('input[type="file"]');
    var files = event.dataTransfer && event.dataTransfer.files;
    if (!input || input.matches(":disabled") || !files || !files.length) return;
    try {
      var transfer = new DataTransfer();
      transfer.items.add(files[0]);
      input.files = transfer.files;
    } catch (err) {
      input.files = files;      // older engines: the server keeps the first file anyway
    }
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });

  function ensureBar(form) {
    var bar = form.querySelector(".upload-bar");
    if (!bar) {
      bar = document.createElement("div");
      bar.className = "upload-bar";
      bar.innerHTML = '<span class="upload-bar__fill"></span>';
      var zone = form.querySelector("[data-dropzone]");
      (zone || form).insertAdjacentElement(zone ? "afterend" : "beforeend", bar);
    }
    var text = form.querySelector(".upload-bar__text");
    if (!text) {
      text = document.createElement("p");
      text.className = "upload-bar__text";
      text.setAttribute("aria-live", "polite");
      bar.insertAdjacentElement("afterend", text);
    }
    bar.setAttribute("role", "progressbar");
    bar.setAttribute("aria-valuemin", "0");
    bar.setAttribute("aria-valuemax", "100");
    bar.setAttribute("aria-label", "Envoi du fichier");
    text.setAttribute("aria-live", "polite");
    bar.hidden = false;
    text.hidden = false;
    return { bar: bar, fill: bar.querySelector(".upload-bar__fill"), text: text };
  }

  function setProgress(ui, ratio) {
    if (ratio >= 1) { setPreparing(ui); return; }
    var percent = Math.min(99, Math.round(Math.max(0, ratio) * 100));
    if (ui.fill) ui.fill.style.setProperty("--upload-progress", String(percent / 100));
    ui.bar.setAttribute("aria-valuenow", String(percent));
    ui.text.textContent = "Envoi du fichier… " + percent + " %";
  }

  function setPreparing(ui) {
    ui.bar.classList.add("is-preparing");
    ui.bar.removeAttribute("aria-valuenow");
    ui.bar.setAttribute("aria-label", "Préparation du CV");
    ui.text.textContent = "Envoi terminé. Préparation du CV pour l’analyse…";
  }

  // `form.action` is shadowed by the upload button named "action".
  function formUrl(form) {
    return form.getAttribute("action") || window.location.href;
  }

  function renderUploadErrors(form, html) {
    var page = new DOMParser().parseFromString(html, "text/html");
    var replacement = page.querySelector("form[data-upload-form]");
    var error = replacement && replacement.querySelector(".field__error, .flash.t-red");
    if (!error) return false;
    form.replaceWith(replacement);
    updateGate(replacement);
    error.setAttribute("tabindex", "-1");
    error.focus();
    return true;
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.hasAttribute("data-upload-form")) return;
    if (form.dataset.uploading) { event.preventDefault(); return; }
    var submitter = event.submitter;
    if (submitter && submitter.value === "skip") return;
    var file = form.querySelector('input[type="file"]');
    if (!file || !file.files || !file.files.length || !window.FormData) return;
    event.preventDefault();
    form.dataset.uploading = "1";

    var data = new FormData(form);
    if (submitter && submitter.name) data.append(submitter.name, submitter.value);
    var ui = ensureBar(form);
    form.querySelectorAll('fieldset, button[type="submit"]').forEach(function (control) { control.disabled = true; });
    setProgress(ui, 0);

    function uploadFailure() {
      // The server may already have saved the upload. Keep this form locked
      // until a GET checks the result, so a lost response cannot duplicate it.
      ui.bar.hidden = true;
      ui.text.textContent = "Impossible de confirmer l’envoi. Vérifie si ton CV a été enregistré avant de réessayer.";
      var recovery = document.createElement("a");
      recovery.className = "upload-bar__recovery";
      recovery.href = form.dataset.recoveryUrl || formUrl(form);
      recovery.textContent = "Vérifier mon CV";
      ui.text.appendChild(recovery);
    }

    var xhr = new XMLHttpRequest();
    xhr.open("POST", formUrl(form));
    // The server answers such a request with 204 + the address to go to, so
    // the redirect is not followed here and its messages reach the visitor.
    xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
    xhr.timeout = 120000;
    xhr.upload.addEventListener("progress", function (progress) {
      if (progress.lengthComputable) setProgress(ui, progress.loaded / progress.total);
    });
    xhr.upload.addEventListener("load", function () { setPreparing(ui); });
    xhr.addEventListener("load", function () {
      var next = xhr.status === 204 && xhr.getResponseHeader("X-Onboarding-Redirect");
      if (next) {
        setPreparing(ui);
        window.location.assign(next);
      } else if (xhr.status >= 200 && xhr.status < 400) {
        if (renderUploadErrors(form, xhr.responseText)) return;
        setPreparing(ui);
        window.location.assign(xhr.responseURL || formUrl(form));
      } else {
        uploadFailure();
      }
    });
    xhr.addEventListener("error", uploadFailure);
    xhr.addEventListener("abort", uploadFailure);
    xhr.addEventListener("timeout", uploadFailure);
    xhr.send(data);
  });

  // --- 5. CV analysis progress --------------------------------------------
  function pollAnalysis(card) {
    if (!card || !card.isConnected || !card.dataset.statusUrl
        || (card.dataset.status !== "pending" && card.dataset.status !== "running")) return;
    window.setTimeout(function () {
      if (!card.isConnected) return;
      var controller = new AbortController();
      var timeout = window.setTimeout(function () { controller.abort(); }, 15000);
      window.fetch(card.dataset.statusUrl, {
        credentials: "same-origin", cache: "no-store", redirect: "error", signal: controller.signal,
        headers: { "X-Requested-With": "XMLHttpRequest" }
      }).then(function (response) {
        if (!response.ok || response.redirected) throw new Error("Analysis status unavailable");
        return response.text();
      }).then(function (html) {
        if (!card.isConnected) return;
        var page = new DOMParser().parseFromString(html, "text/html");
        var replacement = page.querySelector("[data-cv-analysis]");
        if (!replacement || replacement.dataset.documentId !== card.dataset.documentId) {
          throw new Error("Analysis document changed");
        }
        // Leave unchanged live regions in place so screen readers do not
        // announce the same worker stage every two seconds.
        if (replacement.outerHTML !== card.outerHTML) {
          card.replaceWith(replacement);
          card = replacement;
        }
        pollAnalysis(card);
      }).catch(function () {
        if (!card.isConnected) return;
        var warning = card.querySelector(".cv-analysis__connection");
        if (warning) warning.hidden = false;
      }).finally(function () { window.clearTimeout(timeout); });
    }, 2000);
  }

  // --- Initial state -------------------------------------------------------
  document.querySelectorAll(".onb form").forEach(function (form) {
    updateGate(form);
    syncSkipLabel(form);
  });
  document.querySelectorAll("[data-cv-analysis]").forEach(pollAnalysis);
})();

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
       Errors, and a response that re-renders the form with a `.field__error`,
       fall back to a native submit. A skip submit is left alone.

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
    if (button) button.disabled = !hasChoice(form);
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
    if (!input || !files || !files.length) return;
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
    bar.hidden = false;
    text.hidden = false;
    return { bar: bar, fill: bar.querySelector(".upload-bar__fill"), text: text };
  }

  function setProgress(ui, ratio) {
    var percent = Math.round(Math.min(1, Math.max(0, ratio)) * 100);
    if (ui.fill) ui.fill.style.setProperty("--upload-progress", String(percent / 100));
    ui.bar.setAttribute("aria-valuenow", String(percent));
    ui.text.textContent = "Envoi du fichier… " + percent + " %";
  }

  // `form.action` / `form.submit` are shadowed by fields named "action" or
  // "submit" (the upload button is name=action): go through the attribute and
  // the prototype instead.
  function formUrl(form) {
    return form.getAttribute("action") || window.location.href;
  }

  function reRenderedWithErrors(html) {
    try {
      return new DOMParser().parseFromString(html, "text/html").querySelector(".field__error") !== null;
    } catch (err) {
      return html.indexOf("field__error") !== -1;
    }
  }

  function submitNatively(form, submitter) {
    if (submitter && submitter.name && !form.querySelector('input[type="hidden"][name="' + submitter.name + '"]')) {
      var carrier = document.createElement("input");
      carrier.type = "hidden"; carrier.name = submitter.name; carrier.value = submitter.value;
      form.appendChild(carrier);
    }
    HTMLFormElement.prototype.submit.call(form);
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.hasAttribute("data-upload-form")) return;
    var submitter = event.submitter;
    if (submitter && submitter.value === "skip") return;
    var file = form.querySelector('input[type="file"]');
    if (!file || !file.files || !file.files.length || !window.FormData) return;
    event.preventDefault();
    if (form.dataset.uploading) return;
    form.dataset.uploading = "1";

    var data = new FormData(form);
    if (submitter && submitter.name) data.append(submitter.name, submitter.value);
    var ui = ensureBar(form);
    var button = primaryOf(form);
    if (button) button.disabled = true;
    setProgress(ui, 0);

    function fallback() {
      delete form.dataset.uploading;
      submitNatively(form, submitter);
    }

    var xhr = new XMLHttpRequest();
    xhr.open("POST", formUrl(form));
    // The server answers such a request with 204 + the address to go to, so
    // the redirect is not followed here and its messages reach the visitor.
    xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
    xhr.upload.addEventListener("progress", function (progress) {
      if (progress.lengthComputable) setProgress(ui, progress.loaded / progress.total);
    });
    xhr.addEventListener("load", function () {
      var next = xhr.status === 204 && xhr.getResponseHeader("X-Onboarding-Redirect");
      if (next) {
        setProgress(ui, 1);
        window.location.assign(next);
      } else if (xhr.status >= 200 && xhr.status < 400 && !reRenderedWithErrors(xhr.responseText)) {
        setProgress(ui, 1);
        window.location.assign(xhr.responseURL || formUrl(form));
      } else {
        // A re-rendered form (validation error): let the server render it as a
        // full page instead of losing the message.
        fallback();
      }
    });
    xhr.addEventListener("error", fallback);
    xhr.addEventListener("abort", fallback);
    xhr.send(data);
  });

  // --- Initial state -------------------------------------------------------
  document.querySelectorAll(".onb form").forEach(function (form) {
    updateGate(form);
    syncSkipLabel(form);
  });
})();

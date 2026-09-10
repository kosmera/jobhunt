/* JobHunt — progressive field widgets shared by the questionnaire (« Bienvenue »)
   and the settings page. Without this file every widget is a plain form
   control: a comma-separated text field instead of chips, native checkboxes,
   a native number input. Loaded with `defer` after app.js, before any script
   that reads the state these widgets keep in sync (onboarding.js).

   Data-attribute contract (element → behaviour):

   input[data-chips="<id>"][data-chips-name="titles"][data-chips-max="10"]
       Combobox over the suggestions of `<script type="application/json"
       id="<id>">` (Django `json_script`; strings, `[value, detail]` pairs or
       `{value, label, detail}` objects). A chosen value becomes a
       `.chip.is-on` holding `<input type="hidden" name="<data-chips-name>">`
       inside the form's `.chip-row[data-chips-row="<data-chips-name>"]`
       (created after the input when absent). Any `list=` attribute is dropped
       once enhanced. A chip is a label around a checked, visually hidden
       checkbox named `<data-chips-name>` — without JavaScript, unticking it
       removes the entry on submit. Server-rendered chips use the same markup:
       `<label class="chip chip--picked"><input type="checkbox" … checked>Titre
       <button type="button" class="chip__remove" aria-label="Retirer Titre">…`.
   button[type=button][data-chips-add="<value>"][data-chips-for="titles"]
       A recommended chip (« Postes proches »): click adds the value to the
       chips input of the same form named by `data-chips-for` (the first one
       when absent); the button hides while its value is chosen.
   .chip__remove
       Removes its `.chip` (and the hidden input inside) and hands the focus
       back to the chips input of that row.
   input[type=checkbox][data-check-all]
       The « Tout ça » box: checks or unchecks every other checkbox of its
       group; unchecking one of them clears it; checking them all checks it.
   input[type=checkbox][data-exclusive]
       Exclusive with the other checkboxes of its group, in both directions
       (« tous les secteurs », « peu importe »).
       A group is the closest `fieldset`, else the form — and only the boxes
       whose closest fieldset is that one (a nested fieldset is its own group).
   [data-salary]
       Radios `name=salary_period` (values hour|month|year; optional data-min,
       data-max, data-step, data-ceiling, data-unit override the defaults)
       rebind the bounds of the first `input[type=number]` and the
       `[data-salary-unit]` text. An `input[type=range]` (render it `hidden`,
       without a name) is revealed, kept out of the tab order and mirrored
       with the number input.
   .field__error
       On load the input it describes (`aria-describedby`, else the input of
       its `.field`) receives focus. */

(function () {
  "use strict";

  // --- 1. Check-all and exclusive boxes ------------------------------------
  function groupOf(input) {
    return input.closest("fieldset") || input.form;
  }

  function syncGroup(changed) {
    var group = groupOf(changed);
    if (!group) return;
    // Only the boxes whose own group this is: a nested fieldset is another question.
    var boxes = Array.prototype.filter.call(group.querySelectorAll('input[type="checkbox"]'), function (box) {
      return groupOf(box) === group;
    });
    var all = boxes.filter(function (box) { return box.hasAttribute("data-check-all"); })[0] || null;
    var exclusives = boxes.filter(function (box) { return box.hasAttribute("data-exclusive"); });
    var plain = boxes.filter(function (box) { return box !== all && exclusives.indexOf(box) === -1; });
    if (changed === all) {
      plain.forEach(function (box) { box.checked = all.checked; });
      if (all.checked) exclusives.forEach(function (box) { box.checked = false; });
    } else if (exclusives.indexOf(changed) !== -1) {
      if (changed.checked) boxes.forEach(function (box) { if (box !== changed) box.checked = false; });
    } else {
      if (all) all.checked = plain.length > 0 && plain.every(function (box) { return box.checked; });
      if (changed.checked) exclusives.forEach(function (box) { box.checked = false; });
    }
  }

  document.addEventListener("change", function (event) {
    var input = event.target;
    if (input.type === "checkbox" && input.form) syncGroup(input);
  });

  // --- 2. Chips combobox ---------------------------------------------------
  function fold(text) {
    return String(text).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
  }

  function asOption(raw) {
    if (typeof raw === "string") return { value: raw, label: raw, detail: "" };
    if (Array.isArray(raw)) return { value: String(raw[0]), label: String(raw[0]), detail: raw[1] ? String(raw[1]) : "" };
    if (raw && raw.value) return { value: String(raw.value), label: String(raw.label || raw.value), detail: raw.detail ? String(raw.detail) : "" };
    return null;
  }

  function makeChip(name, value) {
    var chip = document.createElement("label");
    chip.className = "chip chip--picked";
    var hidden = document.createElement("input");
    hidden.type = "checkbox"; hidden.className = "visually-hidden";
    hidden.name = name; hidden.value = value; hidden.checked = true;
    chip.appendChild(hidden);
    chip.appendChild(document.createTextNode(value));
    var remove = document.createElement("button");
    remove.type = "button"; remove.className = "chip__remove";
    remove.setAttribute("aria-label", "Retirer " + value);
    remove.innerHTML = '<svg aria-hidden="true"><use href="#i-x"></use></svg>';
    chip.appendChild(hidden);
    chip.appendChild(remove);
    return chip;
  }

  // One entry per enhanced input: a form may hold several (titles and cities).
  var entries = [];

  function entryFor(button) {
    var form = button.form || button.closest("form");
    var name = button.dataset.chipsFor;
    for (var i = 0; i < entries.length; i++) {
      if (entries[i].form === form && (!name || entries[i].name === name)) return entries[i];
    }
    return null;
  }

  function entryOfRow(row) {
    for (var i = 0; i < entries.length; i++) {
      if (entries[i].row === row) return entries[i];
    }
    return null;
  }

  function initChips(input) {
    var form = input.form;
    if (!form) return;
    var name = input.dataset.chipsName || "items";
    var max = parseInt(input.dataset.chipsMax, 10) || 10;
    var options = [];
    var source = document.getElementById(input.dataset.chips);
    try { options = JSON.parse(source.textContent).map(asOption).filter(Boolean); } catch (err) { options = []; }

    var wrap = document.createElement("div");
    wrap.className = "chips-search";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    var menu = document.createElement("ul");
    menu.className = "chips-search__menu";
    menu.id = (input.id || name) + "-listbox";
    menu.setAttribute("role", "listbox");
    menu.hidden = true;
    wrap.appendChild(menu);

    var row = form.querySelector('[data-chips-row="' + name + '"]');
    if (!row) {
      row = document.createElement("div");
      row.className = "chip-row";
      row.setAttribute("data-chips-row", name);
      wrap.parentNode.insertBefore(row, wrap.nextSibling);
    }

    input.removeAttribute("list");
    input.setAttribute("autocomplete", "off");
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-haspopup", "listbox");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", menu.id);

    var active = -1;
    var entry = { input: input, form: form, name: name, row: row, add: add, sync: syncRecommended };
    entries.push(entry);

    function chosen() {
      // A chip is a checked box (unchecked without JavaScript = removed on submit).
      return Array.prototype.map.call(row.querySelectorAll("input:checked"), function (box) { return fold(box.value); });
    }

    function syncRecommended() {
      var picked = chosen();
      form.querySelectorAll("[data-chips-add]").forEach(function (button) {
        if (entryFor(button) !== entry) return;
        button.hidden = picked.indexOf(fold(button.dataset.chipsAdd)) !== -1;
      });
    }

    function close() {
      menu.hidden = true;
      menu.innerHTML = "";
      active = -1;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
    }

    function setActive(index) {
      var items = menu.children;
      if (!items.length) return;
      active = (index + items.length) % items.length;
      Array.prototype.forEach.call(items, function (item, i) {
        item.setAttribute("aria-selected", i === active ? "true" : "false");
      });
      input.setAttribute("aria-activedescendant", items[active].id);
      if (items[active].scrollIntoView) items[active].scrollIntoView({ block: "nearest" });
    }

    function render(query) {
      var q = fold(query);
      var picked = chosen();
      var matches = options.filter(function (o) {
        return picked.indexOf(fold(o.value)) === -1 && (q === "" || fold(o.label).indexOf(q) !== -1);
      });
      if (q) {
        matches.sort(function (a, b) {
          return (fold(b.label).indexOf(q) === 0) - (fold(a.label).indexOf(q) === 0);
        });
      }
      matches = matches.slice(0, 8);
      menu.innerHTML = "";
      matches.forEach(function (o, i) {
        var item = document.createElement("li");
        item.className = "chips-search__item";
        item.id = menu.id + "-" + i;
        item.setAttribute("role", "option");
        item.setAttribute("aria-selected", "false");
        item.dataset.value = o.value;
        item.textContent = o.label;
        if (o.detail) {
          var detail = document.createElement("small");
          detail.textContent = "— " + o.detail;
          item.appendChild(detail);
        }
        menu.appendChild(item);
      });
      active = -1;
      menu.hidden = matches.length === 0;
      input.setAttribute("aria-expanded", menu.hidden ? "false" : "true");
      input.removeAttribute("aria-activedescendant");
    }

    function add(value) {
      value = String(value).trim();
      if (!value || chosen().indexOf(fold(value)) !== -1 || chosen().length >= max) return;
      row.appendChild(makeChip(name, value));
      syncRecommended();
    }

    input.addEventListener("input", function () { render(input.value); });
    input.addEventListener("blur", close);
    input.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (menu.hidden) render(input.value);
        setActive(event.key === "ArrowDown" ? active + 1 : active - 1);
      } else if (event.key === "Escape") {
        if (!menu.hidden) { event.preventDefault(); close(); }
      } else if (event.key === "Enter") {
        if (active >= 0 && !menu.hidden) {
          event.preventDefault();
          add(menu.children[active].dataset.value);
          input.value = "";
          close();
        } else if (input.value.trim()) {
          event.preventDefault();
          input.value.split(",").forEach(add);
          input.value = "";
          close();
        }
        // An empty field lets Enter submit the form, as it does without JS.
      }
    });
    // pointerdown on an option must not blur the input before the click lands.
    menu.addEventListener("pointerdown", function (event) { event.preventDefault(); });
    menu.addEventListener("click", function (event) {
      var item = event.target.closest(".chips-search__item");
      if (!item) return;
      add(item.dataset.value);
      input.value = "";
      close();
      input.focus();
    });

    syncRecommended();
  }

  document.querySelectorAll("input[data-chips]").forEach(initChips);

  // Recommended chips and removals are delegated once: a click reaches the
  // entry it belongs to, never every entry of the form.
  document.addEventListener("click", function (event) {
    var recommended = event.target.closest("[data-chips-add]");
    if (recommended) {
      var target = entryFor(recommended);
      if (target) target.add(recommended.dataset.chipsAdd);
      return;
    }
    var remove = event.target.closest(".chip__remove");
    if (!remove) return;
    event.preventDefault();
    var chip = remove.closest(".chip");
    var row = chip && chip.closest("[data-chips-row]");
    if (chip) chip.remove();
    var owner = row && entryOfRow(row);
    if (owner) {
      owner.sync();
      owner.input.focus();
    }
  });

  // --- 3. Salary -----------------------------------------------------------
  var PERIODS = {
    hour:  { min: 1, max: 500,     step: 1, unit: "€ brut / h",    range: [10, 100, 1] },
    month: { min: 1, max: 50000,   step: 1, unit: "€ brut / mois", range: [1000, 10000, 50] },
    year:  { min: 1, max: 1000000, step: 1, unit: "€ brut / an",   range: [15000, 150000, 500] },
  };

  function initSalary(root) {
    var amount = root.querySelector('input[type="number"]');
    var range = root.querySelector('input[type="range"]');
    var unit = root.querySelector("[data-salary-unit]");
    if (!amount) return;
    if (range) {
      range.hidden = false;
      range.setAttribute("aria-hidden", "true");
      range.tabIndex = -1;
    }

    function rebind() {
      var radio = root.querySelector('input[name="salary_period"]:checked');
      var spec = PERIODS[radio ? radio.value : "month"] || PERIODS.month;
      var data = radio ? radio.dataset : {};
      // The number input accepts anything the form does (1 up to the ceiling);
      // the data-min/max/step of the period describe the slider's window.
      amount.min = spec.min;
      amount.max = data.ceiling || spec.max;
      amount.step = spec.step;
      if (unit) unit.textContent = data.unit || spec.unit;
      if (range) {
        range.min = data.min || spec.range[0];
        range.max = data.max || spec.range[1];
        range.step = data.step || spec.range[2];
        range.value = amount.value || range.min;
      }
    }

    root.addEventListener("change", function (event) {
      if (event.target.name === "salary_period") rebind();
    });
    amount.addEventListener("input", function () { if (range) range.value = amount.value; });
    if (range) range.addEventListener("input", function () { amount.value = range.value; });
    rebind();
  }

  document.querySelectorAll("[data-salary]").forEach(initSalary);

  // --- 4. Focus the first invalid field; otherwise leave the browser's default.
  var error = document.querySelector(".field__error");
  if (error) {
    var target = error.id ? document.querySelector('[aria-describedby~="' + error.id + '"]') : null;
    var field = error.closest(".field") || error.parentElement;
    if (!target && field) {
      // The control the field's label points at, else its first control.
      var label = field.querySelector("label[for]");
      target = label && document.getElementById(label.htmlFor);
      if (!target) target = field.querySelector('input:not([type="hidden"]), select, textarea');
    }
    if (target) target.focus();
  }
})();

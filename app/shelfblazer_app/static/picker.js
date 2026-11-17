// Reference picker: turns a plain "PL-142, PL-12" text field into chips with type-ahead search.
// Progressive enhancement: without JavaScript the original input keeps working. The original input stays in the form
// (hidden) and always holds the selected IDs, so the server validates exactly what it validated before.
// All text from the server is inserted with textContent (never as HTML).
(function () {
  var ID_RE = /^[A-Z]{2}-\d+$/;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function parseIds(s) {
    var seen = {}, out = [];
    (s || "").split(/[\s,]+/).forEach(function (x) { if (x && !seen[x]) { seen[x] = 1; out.push(x); } });
    return out;
  }
  function getJSON(url) {
    return fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : { results: [] }; })
      .catch(function () { return { results: [] }; });
  }

  function enhance(input) {
    var to = input.getAttribute("data-ref-to") || "*";
    var multiple = input.getAttribute("data-multiple") !== "0";
    var typeParam = to === "*" ? "" : encodeURIComponent(to);
    var ids = parseIds(input.value), info = {}, items = [], active = -1, timer = null;

    var wrap = el("div", "pick"), chips = el("span", "pick-chips");
    var q = el("input", "pick-q"), list = el("ul", "pick-list"), msg = el("div", "pick-msg muted small");
    q.type = "text"; q.autocomplete = "off"; q.placeholder = "Search by ID or name…";
    q.setAttribute("role", "combobox"); q.setAttribute("aria-autocomplete", "list"); q.setAttribute("aria-expanded", "false");
    list.setAttribute("role", "listbox"); list.id = "pick-" + Math.random().toString(36).slice(2, 8); list.hidden = true;
    q.setAttribute("aria-controls", list.id);
    msg.setAttribute("aria-live", "polite");
    wrap.appendChild(chips); wrap.appendChild(q); wrap.appendChild(list);
    input.hidden = true;
    input.parentNode.insertBefore(wrap, input.nextSibling);
    wrap.parentNode.insertBefore(msg, wrap.nextSibling);

    function sync() { input.value = ids.join(", "); }
    function render() {
      while (chips.firstChild) chips.removeChild(chips.firstChild);
      ids.forEach(function (id) {
        var d = info[id], chip = el("span", "pick-chip");
        if (d) chip.setAttribute("data-hue", d.hue);
        chip.appendChild(el("code", "rid", id));
        if (d && d.name) chip.appendChild(el("span", "pick-name", d.name));
        var x = el("button", "pick-x", "×");
        x.type = "button"; x.setAttribute("aria-label", "Remove " + id);
        x.addEventListener("click", function () { ids = ids.filter(function (i) { return i !== id; }); sync(); render(); q.focus(); });
        chip.appendChild(x); chips.appendChild(chip);
      });
      sync();
    }
    function loadNames() {
      var missing = ids.filter(function (i) { return !info[i]; });
      if (!missing.length) return;
      getJSON("/api/records?ids=" + encodeURIComponent(missing.join(","))).then(function (r) {
        r.results.forEach(function (d) { info[d.id] = d; });
        render();
      });
    }
    function add(d) {
      info[d.id] = d;
      if (multiple) { if (ids.indexOf(d.id) < 0) ids.push(d.id); } else { ids = [d.id]; }
      q.value = ""; msg.textContent = ""; close(); render(); q.focus();
    }
    function close() { list.hidden = true; q.setAttribute("aria-expanded", "false"); active = -1; }
    function highlight(n) {
      var lis = list.children;
      if (!lis.length) return;
      active = (n + lis.length) % lis.length;
      for (var i = 0; i < lis.length; i++) lis[i].setAttribute("aria-selected", i === active ? "true" : "false");
      lis[active].scrollIntoView({ block: "nearest" });
    }
    function show(results) {
      items = results.filter(function (d) { return ids.indexOf(d.id) < 0; });
      while (list.firstChild) list.removeChild(list.firstChild);
      items.forEach(function (d, n) {
        var li = el("li", "pick-item"); li.setAttribute("role", "option"); li.setAttribute("aria-selected", "false");
        li.setAttribute("data-hue", d.hue);
        li.appendChild(el("code", "rid", d.id));
        li.appendChild(el("span", "pick-name", d.name || "(no name)"));
        if (to === "*") li.appendChild(el("span", "muted small", d.type_name));
        if (d.archived) li.appendChild(el("span", "badge", "archived"));
        li.addEventListener("mousedown", function (e) { e.preventDefault(); add(d); });   // mousedown: before the input loses focus
        list.appendChild(li);
      });
      if (!items.length) { var none = el("li", "pick-none muted", "No match"); list.appendChild(none); }
      list.hidden = false; q.setAttribute("aria-expanded", "true");
      active = -1;
      if (items.length) highlight(0);
    }
    function search() {
      var text = q.value.trim();
      getJSON("/api/records?q=" + encodeURIComponent(text) + "&type=" + typeParam + "&limit=8").then(function (r) {
        if (q.value.trim() === text) show(r.results);          // ignore answers that arrive late
      });
    }
    function addTyped() {                                       // an ID typed or pasted in full, e.g. "PL-142"
      var text = q.value.trim().replace(/,$/, "").toUpperCase();
      if (!ID_RE.test(text)) return false;
      getJSON("/api/records?ids=" + encodeURIComponent(text) + "&type=" + typeParam).then(function (r) {
        if (r.results.length) add(r.results[0]); else msg.textContent = text + " does not exist (or is not allowed here).";
      });
      return true;
    }

    q.addEventListener("input", function () {
      msg.textContent = "";
      clearTimeout(timer); timer = setTimeout(search, 150);
      if (/,\s*$/.test(q.value)) addTyped();
    });
    q.addEventListener("focus", function () { if (!q.value) search(); });
    q.addEventListener("blur", function () { setTimeout(close, 120); });
    q.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); if (list.hidden) search(); else highlight(active + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); highlight(active - 1); }
      else if (e.key === "Enter") {
        if (!list.hidden && active >= 0 && items[active]) { e.preventDefault(); add(items[active]); }
        else if (q.value.trim()) { e.preventDefault(); if (!addTyped() && items.length === 1) add(items[0]); }
      }
      else if (e.key === "Escape") { close(); }
      else if (e.key === "Backspace" && !q.value && ids.length) { ids.pop(); render(); }
    });
    wrap.addEventListener("click", function (e) { if (e.target === wrap || e.target === chips) q.focus(); });

    render(); loadNames();
  }

  document.querySelectorAll("input[data-ref-to]").forEach(enhance);

  // Storage place: choosing "New place…" at the bottom of the list turns it into a text field (without JavaScript the field is simply always there).
  document.querySelectorAll("input.newplace").forEach(function (text) {
    var sel = text.previousElementSibling;
    if (!sel || sel.tagName !== "SELECT") return;
    function sync() { var on = sel.value === "__new__"; text.hidden = !on; text.required = on; if (on) text.focus(); }
    sel.addEventListener("change", sync);
    text.hidden = sel.value !== "__new__";
  });

  // Choice fields that may grow ("addable" multiselect, e.g. Resistance): the ticked options become removable chips with a dropdown for the rest and
  // "New option…" at the bottom, like the storage place. The checkboxes stay in the form (hidden) and are what is submitted; without JavaScript they stay visible.
  document.querySelectorAll(".checks[data-addable]").forEach(function (checks) {
    var text = checks.nextElementSibling;
    if (!text || !text.classList.contains("newopt")) return;
    var boxes = Array.prototype.slice.call(checks.querySelectorAll("input[type=checkbox]"));
    var label = function (b) { return b.parentNode.textContent.trim(); };
    var wrap = el("span", "mselect"), chips = el("span", "chips"), sel = document.createElement("select");
    sel.setAttribute("aria-label", "Add an option");
    checks.hidden = true;
    wrap.appendChild(chips); wrap.appendChild(sel);
    checks.parentNode.insertBefore(wrap, checks);
    text.hidden = true;
    function render() {
      chips.textContent = "";
      boxes.forEach(function (b) {
        if (!b.checked) return;
        var chip = el("span", "chip", label(b) + " "), x = el("button", "chipx", "×");
        x.type = "button"; x.setAttribute("aria-label", "Remove " + label(b));
        x.addEventListener("click", function () { b.checked = false; render(); });
        chip.appendChild(x); chips.appendChild(chip);
      });
      sel.textContent = "";
      sel.appendChild(new Option("Add…", ""));
      boxes.forEach(function (b) { if (!b.checked) sel.appendChild(new Option(label(b), b.value)); });
      sel.appendChild(new Option("New option…", "__new__"));
    }
    sel.addEventListener("change", function () {
      if (sel.value === "__new__") { text.hidden = false; text.required = true; text.focus(); render(); sel.value = "__new__"; return; }
      boxes.forEach(function (b) { if (b.value === sel.value) b.checked = true; });
      if (!text.value.trim()) { text.hidden = true; text.required = false; }
      render();
    });
    text.addEventListener("input", function () { text.required = true; });
    render();
  });
})();

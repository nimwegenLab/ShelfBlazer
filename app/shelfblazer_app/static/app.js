// Loaded in <head> on every page (the policy forbids inline scripts).
document.documentElement.classList.add("js");   // lets the CSS fold the menu on narrow screens only when scripting works

// Confirm dialogs for destructive forms.
document.addEventListener("submit", function (e) {
  var msg = e.target.getAttribute("data-confirm");
  if (msg && !window.confirm(msg)) e.preventDefault();
});

// Menu toggle on narrow screens: the menu opens as a full-screen layer (the button shows an X), the page behind it does not scroll,
// and closing it (the X or Esc) puts the focus back on the button.
function setMenu(header, open) {
  var was = header.classList.contains("open");
  header.classList.toggle("open", open);
  document.body.classList.toggle("menu-open", open);
  var b = header.querySelector(".menu-toggle");
  if (b) {
    b.setAttribute("aria-expanded", open ? "true" : "false");
    if (was && !open) b.focus();
  }
}
document.addEventListener("click", function (e) {
  var b = e.target.closest(".menu-toggle");
  if (b) { var h = b.closest("header"); setMenu(h, !h.classList.contains("open")); }
});
document.addEventListener("keydown", function (e) {
  if (e.key === "Escape") document.querySelectorAll("header.open").forEach(function (h) { setMenu(h, false); });
});

// A whole table row opens its record (the ID link still works on its own; links, buttons and text selection are left alone).
document.addEventListener("click", function (e) {
  var row = e.target.closest("tr[data-href]");
  if (!row || e.button !== 0 || e.target.closest("a, button, input, select, textarea, label, summary")) return;
  if (window.getSelection && String(window.getSelection()).length) return;
  var url = row.getAttribute("data-href");
  if (e.metaKey || e.ctrlKey || e.shiftKey) window.open(url, "_blank"); else window.location.href = url;
});

// Copy to clipboard: <button data-copy="#id"> copies the text of that element (a one-time link, an access token, a command). The result is
// said in the .copy-note next to the button, or in #copy-note.
document.addEventListener("click", function (e) {
  var b = e.target.closest("button[data-copy]");
  if (!b) return;
  var src = document.querySelector(b.getAttribute("data-copy")), note = b.parentNode.querySelector(".copy-note") || document.getElementById("copy-note");
  if (!src) return;
  var text = src.textContent.trim();
  function said(ok) { if (note) note.textContent = ok ? "Copied." : "Could not copy: select it and copy it by hand."; }
  function fallback() {                                          // http pages and old browsers have no clipboard API
    var t = document.createElement("textarea");
    t.value = text; t.setAttribute("readonly", ""); t.style.position = "fixed"; t.style.opacity = "0";
    document.body.appendChild(t); t.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
    t.remove(); said(ok);
  }
  if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(function () { said(true); }, fallback);
  else fallback();
});

// Filter a table by typing: <input data-filter="table tbody tr"> hides the rows whose data-text does not contain every word typed.
// Rows marked data-inactive count only while their table has the class "show-inactive" (see the toggle below).
function refilter(box) {
  var words = box.value.toLowerCase().split(/\s+/).filter(Boolean), shown = 0, total = 0, rows = document.querySelectorAll(box.getAttribute("data-filter"));
  rows.forEach(function (r) {
    var table = r.closest("table"), off = r.hasAttribute("data-inactive") && !(table && table.classList.contains("show-inactive"));
    var hay = r.getAttribute("data-text") || "", ok = words.every(function (w) { return hay.indexOf(w) >= 0; });
    r.hidden = !ok;
    if (!off) { total++; if (ok) shown++; }
  });
  var note = document.getElementById("user-filter-count");
  if (note) note.textContent = words.length ? shown + " of " + total : "";
}
document.addEventListener("input", function (e) {
  var box = e.target.closest("input[data-filter]");
  if (box) refilter(box);
});

// A checkbox with data-toggle="#table" data-class="name" switches that class on the element; data-refilter="#box" re-runs a filter box.
document.addEventListener("change", function (e) {
  var cb = e.target.closest("input[type=checkbox][data-toggle]");
  if (!cb) return;
  var target = document.querySelector(cb.getAttribute("data-toggle"));
  if (target) target.classList.toggle(cb.getAttribute("data-class"), cb.checked);
  var box = cb.getAttribute("data-refilter") && document.querySelector(cb.getAttribute("data-refilter"));
  if (box) refilter(box);
});

// Sort a table by clicking a column title: <th data-sort="text|num"> (aria-sort says which column is sorted and how). A cell may carry
// data-v to sort by something other than its text. Rows keep their other attributes, and rows hidden by a filter stay hidden.
document.addEventListener("click", function (e) {
  var btn = e.target.closest("th[data-sort] .sortbtn");
  if (!btn) return;
  var th = btn.closest("th"), table = th.closest("table"), col = th.cellIndex, num = th.getAttribute("data-sort") === "num";
  var dir = th.getAttribute("aria-sort") === "ascending" ? "descending" : "ascending", sign = dir === "ascending" ? 1 : -1;
  table.querySelectorAll("th[data-sort]").forEach(function (h) { h.setAttribute("aria-sort", h === th ? dir : "none"); });
  function key(row) {
    var c = row.cells[col], v = c.hasAttribute("data-v") ? c.getAttribute("data-v") : c.textContent;
    return num ? parseFloat(v) || 0 : v.trim().toLowerCase();
  }
  var body = table.tBodies[0], rows = Array.prototype.slice.call(body.rows);
  rows.sort(function (a, b) {
    var x = key(a), y = key(b);
    return sign * (num ? x - y : x.localeCompare(y, undefined, { numeric: true }));
  });
  rows.forEach(function (r) { body.appendChild(r); });
});

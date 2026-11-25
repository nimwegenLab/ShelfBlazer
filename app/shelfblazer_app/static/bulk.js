// Bulk edit: row selection on list pages, and showing only the relevant options on the bulk page.
(function () {
  // ---- list page: selection. Hidden until "Select" is pressed; then checkboxes and a bar (Edit selected, count, Clear, Done) show.
  // The chosen IDs are kept in sessionStorage (per record type), so they survive a change of filter, sort or page:
  // filter, tick the rows, change the filter, tick more. Rows not on screen are sent along as hidden fields when the form is submitted.
  var toggle = document.getElementById("bulk-toggle"), main = document.querySelector("main");
  var base = toggle ? toggle.getAttribute("data-key") : null, modeKey = "shelfblazer-selecting:" + base, idsKey = "shelfblazer-selected:" + base;
  function store(k, v) { try { if (v === null) sessionStorage.removeItem(k); else sessionStorage.setItem(k, v); } catch (e) { /* private mode: nothing outlives the page */ } }
  function stored(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }
  function setMode(on) {
    if (!toggle) return;
    main.classList.toggle("selecting", on);
    toggle.setAttribute("aria-pressed", on ? "true" : "false");
    store(modeKey, on ? "1" : null);
  }
  if (toggle) {
    setMode(stored(modeKey) === "1");
    toggle.addEventListener("click", function () { setMode(true); });
  }
  function initList() {
  var form = document.getElementById("bulk");
  if (form && toggle) {
    var boxes = Array.prototype.slice.call(document.querySelectorAll("input.row-check"));
    var all = document.getElementById("check-all"), go = document.getElementById("bulk-go"), exp = document.getElementById("bulk-export"), mv = document.getElementById("bulk-move"), count = document.getElementById("bulk-count");
    var clear = document.getElementById("bulk-clear"), done = document.getElementById("bulk-done"), last = null;
    var sel = {};
    try { (JSON.parse(stored(idsKey)) || []).forEach(function (id) { sel[id] = true; }); } catch (e) { sel = {}; }
    function save() { store(idsKey, JSON.stringify(Object.keys(sel))); }
    function update() {
      var n = Object.keys(sel).length, shown = boxes.filter(function (b) { return b.checked; }).length, away = n - shown;
      go.disabled = exp.disabled = n === 0;
      if (mv) mv.disabled = n === 0;
      all.checked = shown > 0 && shown === boxes.length;
      all.indeterminate = shown > 0 && shown < boxes.length;
      clear.disabled = n === 0;
      count.textContent = n ? n + " selected" + (away > 0 ? " (" + away + " not in this view)" : "") : "";
    }
    function mark(b) { if (b.checked) sel[b.value] = true; else delete sel[b.value]; }
    boxes.forEach(function (b) { b.checked = !!sel[b.value]; });
    all.addEventListener("change", function () { boxes.forEach(function (b) { b.checked = all.checked; mark(b); }); save(); update(); });
    boxes.forEach(function (b, i) {
      b.addEventListener("click", function (e) {              // shift-click selects a range
        if (e.shiftKey && last !== null) {
          var a = Math.min(i, last), z = Math.max(i, last);
          for (var k = a; k <= z; k++) { boxes[k].checked = b.checked; mark(boxes[k]); }
        }
        mark(b); last = i; save(); update();
      });
    });
    function empty() { sel = {}; boxes.forEach(function (b) { b.checked = false; }); save(); update(); }
    clear.addEventListener("click", empty);
    done.addEventListener("click", function () { empty(); setMode(false); });
    form.addEventListener("submit", function (e) {
      Array.prototype.slice.call(form.querySelectorAll("input.sel-extra")).forEach(function (el) { el.remove(); });
      var onScreen = {};
      boxes.forEach(function (b) { onScreen[b.value] = true; });
      Object.keys(sel).forEach(function (id) {
        if (onScreen[id]) return;                              // ticked rows on screen travel with the form already
        var h = document.createElement("input");
        h.type = "hidden"; h.name = "ids"; h.value = id; h.className = "sel-extra";
        form.appendChild(h);
      });
      if (e.submitter === exp) return;                         // exporting changes nothing: the selection stays for the next step
      store(idsKey, null); store(modeKey, null);               // the selection is used up: coming back to the list starts clean
    });
    update();
  }
  }
  initList();
  window.addEventListener("pageshow", function (e) { if (e.persisted) { if (toggle) setMode(stored(modeKey) === "1"); initList(); } });   // back button: show the stored (now empty) selection
  document.addEventListener("results-updated", initList);       // live filtering swaps the table: wire up the new rows

  // ---- bulk page: show the section for the chosen action, and the input for the chosen field
  var page = document.querySelector("form.bulkform");
  if (page) {
    var radios = page.querySelectorAll("input[name=action]"), secs = page.querySelectorAll(".bulk-sec");
    function action() { var r = page.querySelector("input[name=action]:checked"); return r ? r.value : "set"; }
    function refresh() {
      var a = action();
      secs.forEach(function (s) {
        var on = s.getAttribute("data-for").split(" ").indexOf(a) >= 0;
        s.hidden = !on;
        s.querySelectorAll("input, select, textarea").forEach(function (el) { el.disabled = !on; });
        if (on) s.querySelectorAll("select[id$='-field']").forEach(function (sel) {
          s.querySelectorAll(".bulk-val").forEach(function (v) {
            var show = v.getAttribute("data-field") === sel.value;
            v.hidden = !show;
            v.querySelectorAll("input, select, textarea").forEach(function (el) { el.disabled = !show; });
          });
        });
      });
    }
    radios.forEach(function (r) { r.addEventListener("change", refresh); });
    page.querySelectorAll("select[id$='-field']").forEach(function (s) { s.addEventListener("change", refresh); });
    refresh();
  }
})();

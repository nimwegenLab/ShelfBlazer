// Live filtering: lists and the home search update while you type (and when Owner or "archived" change).
// Progressive enhancement: the form still works without JavaScript (Enter / the button). The page is fetched as usual and only
// the #results area (and the count) is swapped, so the server stays the single place that decides what matches.
(function () {
  var form = document.querySelector("form[data-live]"), box = document.getElementById("results");
  if (!form || !box) return;
  var field = form.querySelector("input[type=search]"), timer = null, seq = 0, last = null;

  function target() {
    var p = new URLSearchParams(), path = form.getAttribute("action") || window.location.pathname;
    new FormData(form).forEach(function (v, k) { p.set(k, v); });             // a repeated name (the fuzzy hidden + checkbox pair): the last one wins
    return path + "?" + p.toString();
  }
  function refresh() {
    var u = target();
    if (u === last) return;
    var mine = ++seq;
    fetch(u, { credentials: "same-origin", headers: { Accept: "text/html", "X-Live": "1" } }).then(function (r) {
      if (r.redirected || !r.ok) return null;                              // an exact ID jumps on Enter only; errors leave the page alone
      return r.text();
    }).then(function (html) {
      if (html === null || mine !== seq) return;                            // ignore answers that arrive late
      var doc = new DOMParser().parseFromString(html, "text/html"), fresh = doc.getElementById("results");
      if (!fresh) return;
      box.innerHTML = fresh.innerHTML;
      var t = document.getElementById("total"), nt = doc.getElementById("total");
      if (t && nt) t.textContent = nt.textContent;
      last = u;
      window.history.replaceState(null, "", u);
      document.dispatchEvent(new CustomEvent("results-updated"));
    }).catch(function () { /* offline or server error: keep what is shown */ });
  }
  function soon() { clearTimeout(timer); timer = setTimeout(refresh, 180); }

  // Sequence search (types that offer it): ticking "sequence" greys out and disables "fuzzy" and enables "mismatches", and back.
  var seqBox = form.querySelector("input[name=seq]");
  function sync() {
    var on = seqBox.checked, fz = form.querySelector("input[name=fuzzy][type=checkbox]"), mm = form.querySelector("input[name=mm]");
    fz.disabled = on; fz.parentNode.classList.toggle("off", on);
    mm.disabled = !on; mm.parentNode.classList.toggle("off", !on);
    if (field) field.placeholder = on ? field.getAttribute("data-ph-seq") : field.getAttribute("data-ph");
  }
  if (seqBox) seqBox.addEventListener("change", sync);                         // before the refresh below, so the new state is what is sent

  last = target();
  if (field) field.addEventListener("input", soon);
  form.querySelectorAll("select, input[type=checkbox]").forEach(function (el) { el.addEventListener("change", refresh); });
  var mmBox = form.querySelector("input[name=mm]");
  if (mmBox) mmBox.addEventListener("input", soon);
  form.addEventListener("submit", function () { clearTimeout(timer); });
})();

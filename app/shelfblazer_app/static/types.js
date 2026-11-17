// Type admin: show only the inputs that fit the chosen field type, fill in formula examples, and try a formula on a record.
(function () {
  var sel = document.getElementById("new-type");
  if (sel) {
    var sections = document.querySelectorAll(".addfield .only");
    var refresh = function () {
      sections.forEach(function (s) {
        var on = s.getAttribute("data-types").split(" ").indexOf(sel.value) >= 0;
        s.hidden = !on;
        s.querySelectorAll("input, select, textarea").forEach(function (el) { el.disabled = !on; });
      });
    };
    sel.addEventListener("change", refresh);
    refresh();
  }
  document.querySelectorAll("[data-example]").forEach(function (b) {
    b.addEventListener("click", function () {
      var form = b.closest("form"), input = form.querySelector("input[name=expr]");
      input.value = b.getAttribute("data-example");
      input.focus();
    });
  });
  document.querySelectorAll(".formula-try").forEach(function (box) {
    var out = box.querySelector(".formula-out");
    box.querySelector("[data-try]").addEventListener("click", function () {
      var form = box.closest("form"), expr = form.querySelector("input[name=expr]").value, id = box.querySelector("[data-try-id]").value;
      out.textContent = "…";
      fetch("/admin/types/" + encodeURIComponent(box.getAttribute("data-slug")) + "/preview?expr=" + encodeURIComponent(expr) + "&id=" + encodeURIComponent(id),
            { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (d) { out.textContent = d.error ? "Problem: " + d.error : "= " + (d.value === "" ? "(empty)" : d.value) + "  on " + d.record; })
        .catch(function () { out.textContent = "Could not try the formula."; });
    });
  });
})();

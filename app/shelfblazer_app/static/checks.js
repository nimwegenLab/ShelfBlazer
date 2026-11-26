/* "Check before saving": the Use "..." buttons put a suggested value into the form field it is about. */
(function () {
  document.querySelectorAll(".checkpanel .apply").forEach(function (btn) {
    var key = btn.dataset.field, form = btn.form;
    var el = !key ? null : key === "name" || key === "notes" ? form.elements[key] : form.elements["f_" + key];
    if (!el || (el.tagName !== "INPUT" && el.tagName !== "TEXTAREA") || el.type === "checkbox" || el.type === "hidden") {
      btn.hidden = true;                                  // a field this cannot fill (a choice, a link): the person edits it
      return;
    }
    btn.addEventListener("click", function () {
      el.value = btn.dataset.value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      btn.disabled = true;
      btn.textContent = "Applied";
      el.focus();
    });
  });
})();

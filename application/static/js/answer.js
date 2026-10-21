// Answering screens (user/annotation_task.html, user/annotate_image.html): keyboard shortcuts.
// A separate file because the Content-Security-Policy allows scripts only from our own files.
// Without JavaScript the forms work as plain forms.
(function () {
  var form = document.querySelector(".answer-form");
  if (!form) return;

  document.querySelectorAll("[data-keyboard-hint]").forEach(function (hint) { hint.hidden = false; });

  function submit() {
    if (form.requestSubmit) form.requestSubmit(); else form.submit();
  }

  // 1-9 picks a choice in the question being answered: the one with focus, else the first without an answer.
  var sets = Array.prototype.slice.call(form.querySelectorAll("[data-choices]"));
  function currentSet() {
    var focused = sets.filter(function (set) { return set.contains(document.activeElement); })[0];
    return focused || sets.filter(function (set) { return !set.querySelector("input:checked"); })[0];
  }

  document.addEventListener("keydown", function (e) {
    var typing = e.target.tagName === "TEXTAREA" || (e.target.tagName === "INPUT" && e.target.type === "text");

    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submit(); return; }
    if (e.key === "Enter" && e.target.type === "radio") { e.preventDefault(); submit(); return; }

    var set = !typing && !e.ctrlKey && !e.metaKey && !e.altKey && /^[1-9]$/.test(e.key) ? currentSet() : null;
    if (set) {
      var radio = set.querySelectorAll("input[type=radio]")[Number(e.key) - 1];
      if (radio) { radio.checked = true; radio.focus(); e.preventDefault(); }
    }
  });

  // Don't send the same answer twice on a double click.
  form.addEventListener("submit", function () {
    var button = form.querySelector(".answer-submit");
    if (button) setTimeout(function () { button.disabled = true; }, 0);
  });
  // Back button: the browser may restore this page as it was, button and all.
  window.addEventListener("pageshow", function () {
    var button = form.querySelector(".answer-submit");
    if (button) button.disabled = false;
  });
})();

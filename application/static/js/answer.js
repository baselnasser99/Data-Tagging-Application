// Answering screens (user/annotation_task.html, user/annotate_image.html): keyboard shortcuts.
// A separate file because the Content-Security-Policy allows scripts only from our own files.
// Without JavaScript the forms work as plain forms.
(function () {
  var form = document.querySelector(".answer-form");
  if (!form) return;

  document.querySelectorAll("[data-keyboard-hint]").forEach(function (hint) { hint.hidden = false; });

  // Zoom: the button, a click on the picture or Z opens it full size; Esc, Z or a click closes it.
  var zoomButton = document.querySelector("[data-zoom]");
  var zoom = zoomButton && document.getElementById(zoomButton.dataset.zoom);
  if (zoom && zoom.showModal) {
    zoomButton.hidden = false;
    var openZoom = function () { if (!zoom.open) zoom.showModal(); };
    zoomButton.addEventListener("click", openZoom);
    var picture = document.querySelector(".item-stage .item-media");
    if (picture) { picture.classList.add("is-zoomable"); picture.addEventListener("click", openZoom); }
    zoom.addEventListener("click", function (e) { if (e.target.tagName !== "BUTTON") zoom.close(); });
  }

  function submit() {
    if (form.requestSubmit) form.requestSubmit(); else form.submit();
  }

  // 1-9 picks a choice in the question being answered: a focused one still open, else the first without an
  // answer, so 2 then 1 answers two questions instead of changing the first one twice.
  var sets = Array.prototype.slice.call(form.querySelectorAll("[data-choices]"));
  function currentSet() {
    if (sets.length === 1) return sets[0];
    var focused = sets.filter(function (set) { return set.contains(document.activeElement); })[0];
    if (focused && !focused.querySelector("input:checked")) return focused;
    return sets.filter(function (set) { return !set.querySelector("input:checked"); })[0];
  }

  document.addEventListener("keydown", function (e) {
    var typing = e.target.tagName === "TEXTAREA" || (e.target.tagName === "INPUT" && e.target.type === "text");

    if (zoom && zoom.showModal && !typing && !e.ctrlKey && !e.metaKey && !e.altKey && (e.key === "z" || e.key === "Z")) {
      e.preventDefault();
      if (zoom.open) zoom.close(); else zoom.showModal();
      return;
    }
    if (zoom && zoom.open) return;  // keys pick nothing behind the zoomed picture

    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submit(); return; }
    // Enter with nothing focused submits too, as the hint says; several questions use Ctrl + Enter instead.
    if (e.key === "Enter" && (e.target.type === "radio" || (e.target === document.body && sets.length === 1))) {
      e.preventDefault(); submit(); return;
    }

    var set = !typing && !e.ctrlKey && !e.metaKey && !e.altKey && /^[1-9]$/.test(e.key) ? currentSet() : null;
    if (set) {
      var radio = set.querySelectorAll("input[type=radio]")[Number(e.key) - 1];
      if (radio) {
        radio.checked = true;
        e.preventDefault();
        if (sets.length === 1) { radio.focus(); return; }
        var next = currentSet();  // several questions: bring the next open one into view
        if (next) next.scrollIntoView({ block: "nearest" });
      }
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

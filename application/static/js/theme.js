// Light or dark theme. With no saved choice the stylesheets follow the system setting
// (prefers-color-scheme); choosing Light or Dark in the account menu sets data-theme on
// <html> and remembers it in this browser. Loaded in <head> without defer, so a saved
// choice is applied before the page is drawn and the wrong theme never flashes.
(function () {
  var KEY = 'theme';
  var root = document.documentElement;
  var current;  // 'light', 'dark' or null for the system setting

  function saved() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }  // storage can be blocked
  }
  function apply(choice) {
    current = choice === 'light' || choice === 'dark' ? choice : null;
    if (current) root.setAttribute('data-theme', current);
    else root.removeAttribute('data-theme');
    document.dispatchEvent(new Event('themechange'));  // the weekly chart redraws its colours
  }
  function markButtons() {
    document.querySelectorAll('[data-theme-choice]').forEach(function (button) {
      button.setAttribute('aria-pressed', String(button.dataset.themeChoice === (current || 'system')));
    });
  }

  apply(saved());

  document.addEventListener('DOMContentLoaded', function () {
    markButtons();
    document.querySelectorAll('[data-theme-choice]').forEach(function (button) {
      button.addEventListener('click', function (event) {
        event.stopPropagation();  // keep the account menu open so the change can be seen
        var choice = button.dataset.themeChoice;
        try {
          if (choice === 'system') localStorage.removeItem(KEY);
          else localStorage.setItem(KEY, choice);
        } catch (e) { /* not remembered, but still applied to this page */ }
        apply(choice);
        markButtons();
      });
    });
  });

  // A choice made in another tab applies here too.
  window.addEventListener('storage', function (event) {
    if (event.key === KEY) { apply(event.newValue); markButtons(); }
  });
})();

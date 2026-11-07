// Behaviour shared by every page. Loaded by base.html; the Content-Security-Policy
// allows scripts only from our own static files, so templates carry no inline JS.

// <input data-search-list="ID">: hides items of list ID ([data-search] text) that don't contain every typed word.
document.querySelectorAll('[data-search-list]').forEach(function (input) {
  var list = document.getElementById(input.dataset.searchList);
  var empty = document.getElementById(input.dataset.searchList + '-none');
  input.addEventListener('input', function () {
    var words = input.value.toLowerCase().trim().split(/\s+/).filter(Boolean), shown = 0;
    list.querySelectorAll('[data-search]').forEach(function (item) {
      item.hidden = !words.every(function (w) { return item.dataset.search.indexOf(w) !== -1; });
      if (!item.hidden) shown++;
    });
    if (empty) empty.hidden = shown > 0;
  });
});

// <form data-confirm="Question?">: asks before sending, and sends only on OK.
// Once a form is on its way, its button shows it's busy (a spinner in app.css, and the
// data-busy text if it has one) and can't be pressed twice.
document.addEventListener('submit', function (event) {
  var form = event.target;
  if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) event.preventDefault();
  if (event.defaultPrevented) return;
  var button = event.submitter || form.querySelector('[type=submit]');
  if (!button || button.classList.contains('is-busy')) return;
  button.classList.add('is-busy');
  button.setAttribute('aria-busy', 'true');
  if (button.dataset.busy) { button.dataset.label = button.textContent; button.textContent = button.dataset.busy; }
  setTimeout(function () { button.disabled = true; }, 0);  // after the form has read the button's own value
  setTimeout(function () { unbusy(button); }, 10000);      // a reply that downloads a file leaves the page as it is
});
function unbusy(button) {
  if (!button.classList.contains('is-busy')) return;
  button.classList.remove('is-busy');
  button.removeAttribute('aria-busy');
  if (button.dataset.label) button.textContent = button.dataset.label;
  button.disabled = false;
}
// Back button: the browser may restore the page as it was, busy button and all.
window.addEventListener('pageshow', function () { document.querySelectorAll('.is-busy').forEach(unbusy); });

// The sidebar's "New" menu (base.html). It opens beside the sidebar, or under the button when
// there is no room (phones); it lives in <body> so the sidebar's scrolling never clips it.
(function () {
  var button = document.querySelector('.sidebar-new-button');
  if (!button) return;
  var menu = document.getElementById(button.getAttribute('aria-controls'));
  var items = Array.prototype.slice.call(menu.querySelectorAll('[role=menuitem]'));
  menu.hidden = false;  // shown and hidden by .is-open from here on, so it can fade out
  document.body.appendChild(menu);

  function isOpen() { return menu.classList.contains('is-open'); }
  function place() {
    var r = button.getBoundingClientRect();
    var beside = button.closest('.main-sidebar').getBoundingClientRect().right + 8;
    // phones (AdminLTE's 768px breakpoint): the sidebar slides in, so measure nothing beside it
    var below = window.innerWidth < 768 || beside + menu.offsetWidth + 8 > window.innerWidth;
    menu.classList.toggle('is-below', below);
    menu.style.left = (below ? Math.max(10, r.left) : beside) + 'px';  // max: the sidebar may still be sliding in
    menu.style.top = (below ? r.bottom + 6 : r.top) + 'px';
  }
  function open(focusFirst) {
    menu.classList.add('is-open');
    button.setAttribute('aria-expanded', 'true');
    place();
    if (focusFirst) items[0].focus({ preventScroll: true });
  }
  function close(refocus) {
    if (!isOpen()) return;
    menu.classList.remove('is-open');
    button.setAttribute('aria-expanded', 'false');
    if (refocus) button.focus();
  }

  // detail is 0 when the button was pressed with the keyboard: then the first item gets focus
  button.addEventListener('click', function (e) { isOpen() ? close() : open(e.detail === 0); });
  button.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); open(true); }
  });
  menu.addEventListener('keydown', function (e) {
    var i = items.indexOf(document.activeElement);
    if (e.key === 'Escape') { e.preventDefault(); close(true); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length].focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
    else if (e.key === 'Home') { e.preventDefault(); items[0].focus(); }
    else if (e.key === 'End') { e.preventDefault(); items[items.length - 1].focus(); }
    else if (e.key === 'Tab') close(true);
  });
  document.addEventListener('click', function (e) {
    if (!menu.contains(e.target) && !button.contains(e.target)) close();
  });
  // The menu doesn't follow the page, so it closes when the layout moves under it
  window.addEventListener('resize', function () { close(); });
  button.closest('.main-sidebar').addEventListener('scroll', function () { close(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(isOpen()); });
})();

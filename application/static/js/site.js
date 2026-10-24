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

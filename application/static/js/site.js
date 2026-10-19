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
document.addEventListener('submit', function (event) {
  var form = event.target;
  if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) event.preventDefault();
});

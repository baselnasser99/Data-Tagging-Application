// People page (admin/users.html): search and filter on the page itself; the list is small
// enough to never need a round trip.
(function () {
  var search = document.getElementById('people-search'), filter = document.getElementById('people-filter');
  if (!search || !filter) return;
  var rows = document.querySelectorAll('#people-list .person'), empty = document.getElementById('people-empty');
  var count = document.getElementById('people-count');
  function apply() {
    var words = search.value.toLowerCase().trim().split(/\s+/).filter(Boolean), want = filter.value, shown = 0;
    rows.forEach(function (row) {
      var groups = row.dataset.groups.split(' ');
      var inView = want ? groups.indexOf(want) !== -1 : groups[0] !== 'removed';
      var match = words.every(function (w) { return row.dataset.search.indexOf(w) !== -1; });
      row.hidden = !(inView && match);
      if (!row.hidden) shown++;
    });
    empty.hidden = shown > 0;
    count.textContent = (words.length || want) ? 'Showing ' + shown + ' of ' + rows.length : '';
  }
  search.addEventListener('input', apply);
  filter.addEventListener('change', apply);
  // ?show=none (the side panel's "in no group" link) opens the list already filtered
  var show = new URLSearchParams(location.search).get('show');
  var option = show && Array.prototype.find.call(filter.options, function (o) { return o.value === show; });
  if (option) { filter.value = show; apply(); }
})();

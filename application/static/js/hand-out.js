// Hand out tasks (admin/generate_annotation_tasks.html): warn when the chosen group has
// fewer people than the answers needed per task (data-needed on the select).
(function () {
  var select = document.getElementById('group_id');
  var warning = document.getElementById('size-warning');
  if (!select || !warning) return;
  var needed = parseInt(select.dataset.needed || '1', 10);
  function check() {
    var option = select.options[select.selectedIndex];
    var size = parseInt(option.getAttribute('data-size') || '0', 10);
    warning.hidden = !(option.value && size < needed);
  }
  select.addEventListener('change', check);
  check();  // a pre-selected group is checked too
})();

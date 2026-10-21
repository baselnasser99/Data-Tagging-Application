// Hand out tasks (admin/generate_annotation_tasks.html): before anything is handed out, say who
// will share the work and about how many tasks each person gets, and warn when fewer people
// than the answers needed per task would share it. Data comes from attributes on the select.
(function () {
  var select = document.getElementById('group_id');
  var warning = document.getElementById('size-warning');
  var preview = document.getElementById('share-preview');
  if (!select || !warning) return;
  var needed = parseInt(select.dataset.needed || '1', 10);
  var tasks = parseInt(select.dataset.tasks || '0', 10);
  var cap = parseInt(select.dataset.cap || '0', 10);
  function ids(text) { return (text || '').split(',').filter(Boolean); }
  var current = ids(select.dataset.current);

  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : (many || one + 's')); }

  function check() {
    var option = select.options[select.selectedIndex];
    if (!option || !option.value) { warning.hidden = true; if (preview) preview.hidden = true; return; }
    // Everyone already on the project plus the chosen group, each person counted once.
    var people = {};
    current.concat(ids(option.getAttribute('data-members'))).forEach(function (id) { people[id] = true; });
    var count = Object.keys(people).length;
    warning.hidden = count >= needed;
    if (!preview) return;
    preview.hidden = false;
    if (!count) { preview.textContent = 'This group has nobody in it yet.'; return; }
    var answers = tasks * needed;
    var low = Math.floor(answers / count), high = Math.ceil(answers / count);
    if (cap) { low = Math.min(low, cap); high = Math.min(high, cap); }
    var share = low === high ? String(low) : low + ' to ' + high;
    var work = needed > 1 ? plural(tasks, 'task') + ', ' + needed + ' answers each (' + answers + ' in all)' : plural(tasks, 'task');
    preview.textContent = plural(count, 'person', 'people') + ' will share ' + work + ': about ' + share + ' each.' +
      (cap && answers / count > cap ? ' Some work stays unassigned: it is above the limit of ' + cap + ' per person.' : '');
  }
  select.addEventListener('change', check);
  check();  // a pre-selected group is checked too
})();

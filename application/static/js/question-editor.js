// Each question editor (admin/edit_project_questions.html): show the multiple-choice fields only for
// multiple choice, only as many option boxes as chosen, and ask to confirm deleting answers when an
// existing question's type changes.
document.querySelectorAll('.question-editor').forEach(function (editor) {
  var type = editor.querySelector('.question-type');
  var count = editor.querySelector('.num-options');
  var confirmBox = editor.querySelector('.type-change-confirm');
  var correct = editor.querySelector('.correct-answer');
  function update() {
    var mcq = type.value === 'mcq';
    var n = parseInt(count.value, 10);
    editor.querySelectorAll('.mcq-only').forEach(function (el) { el.hidden = !mcq; });
    editor.querySelectorAll('.text-only').forEach(function (el) { el.hidden = mcq; });
    editor.querySelectorAll('.option-group').forEach(function (el) {
      var shown = mcq && parseInt(el.dataset.option, 10) <= n;
      el.hidden = !shown;
      el.querySelector('input').disabled = !shown;  // hidden options are not sent, so they are cleared
    });
    if (correct) {
      Array.prototype.forEach.call(correct.options, function (o) { o.hidden = parseInt(o.value, 10) > n; });
      if (parseInt(correct.value, 10) > n) correct.value = '1';
    }
    if (confirmBox) confirmBox.hidden = type.value === type.dataset.initial;
  }
  type.addEventListener('change', update);
  count.addEventListener('change', update);
  update();
});

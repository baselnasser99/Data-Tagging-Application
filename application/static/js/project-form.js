// New project (admin/create_annotation_project.html). Questions are added one at a time.
// Multiple choice gets one box per option (2 to 6), so options can't run together the way
// lines in a text box can.
(function () {
  var list = document.getElementById('dynamic-questions');
  if (!list) return;
  var total = document.getElementById('total_questions');
  var questionTemplate = document.getElementById('question-template');
  var optionTemplate = document.getElementById('option-template');

  // Names carry the question's number (question_1, question_type_1, question_options_1 ...),
  // so they are rewritten whenever a question is added or removed.
  function renumber() {
    var cards = list.querySelectorAll('.question-card');
    cards.forEach(function (card, i) {
      var n = i + 1;
      card.querySelector('.question-number').textContent = n;
      var label = card.querySelector('.question-label');
      label.textContent = 'Question ' + n;
      label.htmlFor = 'question_' + n;
      var text = card.querySelector('.question-text');
      text.id = 'question_' + n; text.name = 'question_' + n;
      card.querySelector('.question-type').name = 'question_type_' + n;
      card.querySelectorAll('.option-row input').forEach(function (input, j) {
        input.name = 'question_options_' + n;
        input.setAttribute('aria-label', 'Question ' + n + ', option ' + (j + 1));
      });
    });
    total.value = cards.length;
  }

  function updateOptions(card) {
    var mcq = card.querySelector('.question-type').value === 'mcq';
    var rows = card.querySelectorAll('.option-row');
    card.querySelector('.question-options').hidden = !mcq;
    rows.forEach(function (row, j) {
      var input = row.querySelector('input');
      input.disabled = !mcq;          // free-text questions send no options
      input.required = mcq && j < 2;  // at least two options before the form can be sent
      row.querySelector('.remove-option').disabled = rows.length <= 2;
    });
    card.querySelector('.add-option').disabled = rows.length >= 6;
  }

  function addOption(card) {
    card.querySelector('.option-list').appendChild(optionTemplate.content.cloneNode(true));
    renumber(); updateOptions(card);
  }

  document.getElementById('add-question').addEventListener('click', function () {
    list.appendChild(questionTemplate.content.cloneNode(true));
    var card = list.lastElementChild;
    addOption(card); addOption(card);
    card.querySelector('.question-text').focus();
  });

  list.addEventListener('click', function (event) {
    var card = event.target.closest('.question-card');
    if (!card) return;
    if (event.target.closest('.remove-question')) { card.remove(); renumber(); }
    else if (event.target.closest('.add-option')) { addOption(card); card.querySelector('.option-row:last-child input').focus(); }
    else if (event.target.closest('.remove-option')) { event.target.closest('.option-row').remove(); renumber(); updateOptions(card); }
  });
  list.addEventListener('change', function (event) {
    if (event.target.classList.contains('question-type')) updateOptions(event.target.closest('.question-card'));
  });
})();

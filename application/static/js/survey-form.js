// New survey (admin/create_project.html): add questions, and show the multiple-choice
// fields only for multiple-choice questions.
(function () {
  var questionsContainer = document.getElementById('questions');
  if (!questionsContainer) return;

  function toggleMcqOptions(section) {
    const dropdown = section.querySelector('.dropdown-value');
    const mcqOptions = section.querySelector('.mcq-options');
    const numberOfOptionsInput = section.querySelector('.number-of-options');
    const optionsContainer = section.querySelector('.options-container');

    // Hidden fields are disabled so they are not sent with the form
    dropdown.addEventListener('change', function () {
      const mcq = dropdown.value === 'mcq';
      mcqOptions.style.display = mcq ? 'block' : 'none';
      numberOfOptionsInput.disabled = !mcq;
      mcqOptions.querySelectorAll('input').forEach(input => input.disabled = !mcq);
    });

    numberOfOptionsInput.addEventListener('change', function () {
      populateOptions(optionsContainer, numberOfOptionsInput.value);
    });

    // Set the initial state
    dropdown.dispatchEvent(new Event('change'));
    numberOfOptionsInput.dispatchEvent(new Event('change'));
  }

  function populateOptions(container, numberOfOptions) {
    container.innerHTML = '';
    for (let i = 1; i <= numberOfOptions; i++) {
      const optionInput = document.createElement('input');
      optionInput.type = 'text';
      optionInput.name = `mcq-option-${i}-${container.parentElement.parentElement.querySelector('label').textContent.split(' ')[1]}`;
      optionInput.placeholder = `Option ${i}`;
      optionInput.className = 'form-control mb-2';
      container.appendChild(optionInput);
    }
  }

  document.querySelectorAll('.quiz-section').forEach(section => toggleMcqOptions(section));

  let totalQuestions = 1;

  document.getElementById('add-survey-question').addEventListener('click', function () {
    totalQuestions += 1;
    document.getElementById('total_questions').value = totalQuestions;
    const questionHtml = `
      <div class="quiz-section">
        <label class="form-label">Question ${totalQuestions}</label>
        <textarea rows="1" class="form-control" name="question-${totalQuestions}" placeholder="What do you want to ask?" required></textarea>
        <div class="row" style="margin-top: 10px;">
          <div class="col-sm-6 form-group">
            <label class="form-label">Answer type</label>
            <select name="dropdown-${totalQuestions}" class="form-control dropdown-value">
              <option value="text">Free text</option>
              <option value="mcq">Multiple choice</option>
            </select>
          </div>
          <div class="col-sm-6 form-group">
            <label class="form-label">Attach a file (optional)</label>
            <input type="file" name="question-${totalQuestions}-file" class="form-control">
          </div>
        </div>
        <div class="form-group mcq-options" style="display: none;">
          <label class="form-label">Number of options</label>
          <input type="number" name="number_of_options-${totalQuestions}" class="form-control number-of-options" min="2" max="6" value="2" style="max-width: 120px;">
          <label class="form-label" style="margin-top: 8px;">Options</label>
          <div class="options-container"></div>
        </div>
        <label class="form-label">Correct answer (optional)</label>
        <textarea rows="1" class="form-control" name="answer-${totalQuestions}" placeholder="Only you see this"></textarea>
      </div>
    `;
    const newQuestion = new DOMParser().parseFromString(questionHtml, 'text/html').body.firstElementChild;
    questionsContainer.appendChild(newQuestion);
    toggleMcqOptions(newQuestion);
  });
})();

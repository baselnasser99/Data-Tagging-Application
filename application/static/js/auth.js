// Sign-in page behaviour. A separate file because the Content-Security-Policy allows
// scripts only from our own static files.

// "Show" button on the password field: lets people check what they typed.
document.querySelectorAll(".password-field .reveal").forEach(function (button) {
  var input = document.getElementById(button.getAttribute("aria-controls"));
  if (!input) return;
  button.hidden = false;
  button.addEventListener("click", function () {
    var show = input.type === "password";
    input.type = show ? "text" : "password";
    button.textContent = show ? "Hide" : "Show";
    button.setAttribute("aria-pressed", String(show));
    input.focus();
  });
});

// "Signing in…" while the next page loads, so a slow sign-in doesn't look like nothing happened.
document.querySelectorAll("form").forEach(function (form) {
  form.addEventListener("submit", function () {
    var button = form.querySelector("[type=submit][data-busy]");
    if (!button) return;
    button.dataset.label = button.textContent;
    button.textContent = button.dataset.busy;
    button.classList.add("is-busy");
    button.setAttribute("aria-busy", "true");
  });
});
window.addEventListener("pageshow", function () {
  document.querySelectorAll(".is-busy").forEach(function (button) {
    button.textContent = button.dataset.label;
    button.classList.remove("is-busy");
    button.removeAttribute("aria-busy");
  });
});

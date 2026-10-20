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

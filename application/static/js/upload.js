// Drop zones (admin/upload_images.html, the CSV tab of admin/create_user.html): highlight
// the zone and list the chosen files.
// A separate file because the Content-Security-Policy allows scripts only from our own files.
// The file input itself covers the zone, so choosing and dropping files work without this script.
(function () {
  var zone = document.querySelector("[data-dropzone]");
  if (!zone) return;
  var input = zone.querySelector("input[type=file]");
  var list = document.querySelector("[data-upload-list]");
  var summary = document.querySelector("[data-upload-summary]");
  var files = document.querySelector("[data-upload-files]");
  var questions = Number(zone.getAttribute("data-questions")) || 0;

  function size(bytes) {
    if (bytes < 1024 * 1024) return Math.max(1, Math.round(bytes / 1024)) + " KB";
    return (bytes / 1024 / 1024).toFixed(1) + " MB";
  }
  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

  function show() {
    var chosen = Array.prototype.slice.call(input.files || []);
    list.hidden = chosen.length === 0;
    files.textContent = "";
    var zips = 0;
    chosen.forEach(function (file) {
      var li = document.createElement("li");
      var isZip = /\.zip$/i.test(file.name);
      if (isZip) zips += 1;
      li.textContent = file.name + " · " + size(file.size) + (isZip ? " · unpacked on upload" : "");
      files.appendChild(li);
    });
    var direct = chosen.length - zips;
    var text = plural(chosen.length, "file") + " chosen.";
    if (direct && questions) text += " " + plural(direct, "item") + " × " + plural(questions, "question") + " = " + plural(direct * questions, "task") + (zips ? ", plus what the ZIP files hold." : ".");
    summary.textContent = text;
  }

  input.addEventListener("change", show);
  ["dragenter", "dragover"].forEach(function (type) {
    zone.addEventListener(type, function () { zone.classList.add("is-over"); });
  });
  ["dragleave", "drop"].forEach(function (type) {
    zone.addEventListener(type, function () { zone.classList.remove("is-over"); });
  });
  show();
})();

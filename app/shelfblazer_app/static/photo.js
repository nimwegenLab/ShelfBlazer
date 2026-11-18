// Picture upload: a file input marked data-photo (the "thumbnail" field of a type) shrinks the chosen photo to at most 1000 px, as JPEG,
// in the browser before it is sent: a phone photo of 4 MB becomes about 100 KB, so cards and lists stay light and the data repository small.
// Without canvas support, or for formats the browser cannot decode (HEIC on a desktop), the original file is uploaded unchanged.
(function () {
  "use strict";
  var MAX = 1000, KEEP = 300 * 1024;
  if (!window.DataTransfer || !window.createImageBitmap) return;

  function shrink(file) {
    if (!/^image\/(jpeg|png|webp|heic|heif)$/i.test(file.type) || (file.type === "image/jpeg" && file.size < KEEP)) return Promise.resolve(file);
    return createImageBitmap(file, { imageOrientation: "from-image" }).then(function (bmp) {
      var k = Math.min(1, MAX / Math.max(bmp.width, bmp.height)), c = document.createElement("canvas"), g;
      c.width = Math.round(bmp.width * k); c.height = Math.round(bmp.height * k);
      g = c.getContext("2d"); g.fillStyle = "#fff"; g.fillRect(0, 0, c.width, c.height); g.drawImage(bmp, 0, 0, c.width, c.height);
      return new Promise(function (ok) {
        c.toBlob(function (b) { ok(b && b.size < file.size ? new File([b], file.name.replace(/\.[^.]*$/, "") + ".jpg", { type: "image/jpeg" }) : file); }, "image/jpeg", 0.85);
      });
    }).catch(function () { return file; });
  }

  document.addEventListener("change", function (e) {
    var input = e.target;
    if (!input.matches || !input.matches("input[type=file][data-photo]") || !input.files.length) return;
    Promise.all(Array.prototype.map.call(input.files, shrink)).then(function (files) {
      var dt = new DataTransfer();
      files.forEach(function (f) { dt.items.add(f); });
      input.files = dt.files;
    });
  });
})();

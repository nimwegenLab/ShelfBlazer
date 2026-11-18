// Barcode scanning on the home page: camera -> decoder in this browser -> /scan?c=<text>.
// Only the decoded text leaves the device; no picture is sent anywhere. The decoder is the browser's own BarcodeDetector where it reads
// Code 128, EAN-13 and DataMatrix (Chrome on Android and Mac), else the bundled zxing-wasm (static/vendor/zxing, e.g. iPhone Safari).
// Progressive enhancement: without a camera or a secure page (HTTPS or localhost) the button stays hidden; a USB reader that types the
// code into the search box and presses Enter works regardless.
(function () {
  "use strict";
  var openBtn = document.getElementById("scan-open"), dlg = document.getElementById("scan");
  if (!openBtn || !dlg || !dlg.showModal || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.isSecureContext) return;
  openBtn.classList.add("on");

  var NATIVE = ["code_128", "code_39", "code_93", "ean_13", "ean_8", "upc_a", "upc_e", "itf", "data_matrix", "qr_code", "codabar"];
  var ZXING = ["Code128", "Code39", "Code93", "EAN-13", "EAN-8", "UPC-A", "UPC-E", "ITF", "DataMatrix", "QRCode", "Codabar"];
  var VENDOR = "/static/vendor/zxing/";
  var GRACE = 700;                                   // ms to keep reading after the first hit: a label often carries two or three codes
  var video = dlg.querySelector("video"), msg = dlg.querySelector(".scan-msg");
  var stream = null, decoder = null, active = false, timer = null, found = [], run = 0;

  function nativeDecoder() {
    if (!window.BarcodeDetector || !window.BarcodeDetector.getSupportedFormats) return Promise.resolve(null);
    return window.BarcodeDetector.getSupportedFormats().then(function (have) {
      if (["code_128", "ean_13", "data_matrix"].some(function (f) { return have.indexOf(f) < 0; })) return null;
      var det = new window.BarcodeDetector({ formats: NATIVE.filter(function (f) { return have.indexOf(f) >= 0; }) });
      return function (v) { return det.detect(v).then(function (rs) { return rs.map(function (r) { return r.rawValue; }); }); };
    }).catch(function () { return null; });
  }

  function wasmDecoder() {
    return new Promise(function (ok, fail) {
      if (window.ZXingWASM) return ok();
      var s = document.createElement("script");
      s.src = VENDOR + "zxing-reader.js"; s.onload = ok; s.onerror = function () { fail(new Error("reader")); };
      document.head.appendChild(s);
    }).then(function () {
      var overrides = { locateFile: function (name) { return VENDOR + name; } };      // the wasm file comes from this server, not from the library's default CDN
      if (window.ZXingWASM.prepareZXingModule) window.ZXingWASM.prepareZXingModule({ overrides: overrides }); else window.ZXingWASM.setZXingModuleOverrides(overrides);
      var canvas = document.createElement("canvas"), ctx = canvas.getContext("2d", { willReadFrequently: true });
      return function (v) {
        var k = Math.min(1, 1280 / Math.max(v.videoWidth, v.videoHeight));
        canvas.width = Math.round(v.videoWidth * k); canvas.height = Math.round(v.videoHeight * k);
        ctx.drawImage(v, 0, 0, canvas.width, canvas.height);
        return window.ZXingWASM.readBarcodes(ctx.getImageData(0, 0, canvas.width, canvas.height),
          { formats: ZXING, tryHarder: true, tryRotate: true, tryInvert: true, maxNumberOfSymbols: 6 })
          .then(function (rs) { return rs.filter(function (r) { return r.isValid; }).map(function (r) { return r.text; }); });
      };
    });
  }

  function explain(e) {
    var n = e && e.name;
    if (n === "NotAllowedError" || n === "SecurityError") return "The camera is blocked. Allow it for this site in the browser settings, or type the code in the search box.";
    if (n === "NotFoundError" || n === "OverconstrainedError") return "No camera found. Type the code in the search box instead.";
    if (e && e.message === "reader") return "The barcode reader could not be loaded. Type the code in the search box instead.";
    return "The camera could not be started. Type the code in the search box instead.";
  }

  function stop() {
    active = false; run++;
    clearTimeout(timer); timer = null;
    if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
    stream = null; video.srcObject = null;
  }

  function done() {
    var codes = found.slice(0, 20);
    stop();
    window.location.href = "/scan?" + codes.map(function (c) { return "c=" + encodeURIComponent(c); }).join("&");
  }

  function loop(me) {
    if (!active || me !== run) return;
    decoder(video).then(function (texts) {
      texts.forEach(function (t) { if (t && found.indexOf(t) < 0) found.push(t); });
      if (found.length && !timer) timer = setTimeout(done, GRACE);
    }).catch(function () { /* a frame that fails to decode is skipped */ }).then(function () { setTimeout(function () { loop(me); }, 120); });
  }

  function start() {
    var me = ++run;
    found = []; timer = null; msg.textContent = "Starting the camera…";
    navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false }).then(function (s) {
      if (me !== run || !dlg.open) { s.getTracks().forEach(function (t) { t.stop(); }); throw new Error("closed"); }      // closed while the camera was starting
      stream = s; video.srcObject = s;
      return video.play();
    }).then(function () {
      msg.textContent = "Loading the reader…";
      return decoder || nativeDecoder().then(function (d) { return d || wasmDecoder(); });
    }).then(function (d) {
      if (me !== run) return;
      decoder = d; active = true;
      msg.textContent = "Point the camera at a barcode and hold it steady, close enough to fill about a third of the view.";
      loop(me);
    }).catch(function (e) { if (!e || e.message !== "closed") msg.textContent = explain(e); });
  }

  openBtn.addEventListener("click", function () { dlg.showModal(); start(); });
  dlg.querySelector(".scan-close").addEventListener("click", function () { dlg.close(); });
  dlg.addEventListener("close", stop);                                         // Esc, the Close button and navigating away all end here
  window.addEventListener("pagehide", stop);
  if (/[?&]scan=1(&|$)/.test(window.location.search)) openBtn.click();         // "Scan another" on the result page
})();

// Barcode scanning: camera -> decoder in this browser -> /scan?c=<text> (Home, and the Scan button in the phone header while on Home),
// or the codes are added to a form field (a Scan button marked data-scan-into="<input name>" beside a type's scan fields).
// Only the decoded text leaves the device; no picture is sent anywhere. The decoder is the browser's own BarcodeDetector where it reads
// Code 128, EAN-13 and DataMatrix (Chrome on Android and Mac), else the bundled zxing-wasm (static/vendor/zxing, e.g. iPhone Safari).
// Progressive enhancement: without a camera or a secure page (HTTPS or localhost) the button stays hidden; a USB reader that types the
// code into the search box and presses Enter works regardless.
(function () {
  "use strict";
  var openBtn = document.getElementById("scan-open"), dlg = document.getElementById("scan");
  var intoBtns = Array.prototype.slice.call(document.querySelectorAll("[data-scan-into]"));
  if (!dlg || !dlg.showModal || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.isSecureContext) return;
  if (openBtn) openBtn.classList.add("on");
  intoBtns.forEach(function (b) { b.classList.add("on"); });

  var NATIVE = ["code_128", "code_39", "code_93", "ean_13", "ean_8", "upc_a", "upc_e", "itf", "data_matrix", "qr_code", "codabar"];
  var ZXING = ["Code128", "Code39", "Code93", "EAN-13", "EAN-8", "UPC-A", "UPC-E", "ITF", "DataMatrix", "QRCode", "Codabar"];
  var VENDOR = "/static/vendor/zxing/";
  var GRACE = 700;                                   // ms to keep reading after the first hit: a label often carries two or three codes
  var video = dlg.querySelector("video"), msg = dlg.querySelector(".scan-msg"), pick = dlg.querySelector(".scan-pick");
  var stream = null, decoder = null, active = false, timer = null, found = [], run = 0, target = null;   // target: the input to fill, or null to open /scan

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
    var n = e && e.name, box = target ? "the field" : "the search box";
    if (n === "NotAllowedError" || n === "SecurityError") return "The camera is blocked. Allow it for this site in the browser settings, or type the code in " + box + ".";
    if (n === "NotFoundError" || n === "OverconstrainedError") return "No camera found. Type the code in " + box + " instead.";
    if (e && e.message === "reader") return "The barcode reader could not be loaded. Type the code in " + box + " instead.";
    return "The camera could not be started. Type the code in " + box + " instead.";
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
    if (target) { choose(codes); return; }
    window.location.href = "/scan?" + codes.map(function (c) { return "c=" + encodeURIComponent(c); }).join("&");
  }

  // Filling a field: keep what identifies the product. A GS1 code (DataMatrix, GS1-128) also holds the lot, expiry and serial: only its
  // GTIN and product elements are kept (as scan.py does). EAN/UPC/GTIN digits with a valid check digit and HIBC primary codes are product
  // codes; an HIBC secondary code ("+$...") is a lot; anything else may be a REF or a lot, so the person decides.
  var GS1 = /\((\d{2,4})\)([^()]*)/g, RAW_GTIN = /^01(\d{14})(?=$|\x1d|1[0-7]|2[01]|3\d|9\d|240)/;
  function checkDigitOk(d) {
    var sum = 0;
    for (var i = d.length - 2, w = 3; i >= 0; i--, w = 4 - w) sum += +d[i] * w;
    return (10 - sum % 10) % 10 === +d[d.length - 1];
  }
  function classify(text) {
    var t = (text || "").trim(), m, out = [];
    if (!t) return out;
    if (t.indexOf("(") >= 0) {
      var el = {};
      while ((m = GS1.exec(t))) el[m[1]] = m[2];
      GS1.lastIndex = 0;
      ["01", "240", "91"].forEach(function (a) { if (el[a]) out.push({ code: el[a], product: true, what: a === "01" ? "product code (GTIN), from a GS1 code" : "product number, from a GS1 code" }); });
      if (out.length) return out;
      t = t.replace(/[()]/g, "");
    }
    if ((m = RAW_GTIN.exec(t))) return [{ code: m[1], product: true, what: "product code (GTIN), from a GS1 code; lot and date left out" }];
    if (/^\d{8}$|^\d{12,14}$/.test(t) && checkDigitOk(t)) return [{ code: t, product: true, what: "product code (EAN / UPC / GTIN)" }];
    if (/^\+\$/.test(t)) return [{ code: t, product: false, what: "lot or serial number (HIBC)" }];
    if (/^\+[A-Z]\d{3}/.test(t)) return [{ code: t, product: true, what: "product code (HIBC)" }];
    return [{ code: t, product: false, what: "a REF or a lot number? Check against the box" }];
  }

  function choose(codes) {
    var items = [], seen = {};
    codes.forEach(function (c) { classify(c).forEach(function (it) { if (!seen[it.code]) { seen[it.code] = 1; items.push(it); } }); });
    if (items.length === 1) { add([items[0].code]); return; }                 // one code: the one the camera was pointed at
    var anyProduct = items.some(function (it) { return it.product; }), ul = pick.querySelector("ul");
    ul.textContent = "";
    items.forEach(function (it) {
      var li = document.createElement("li"), lab = document.createElement("label"), box = document.createElement("input"), code = document.createElement("code"), what = document.createElement("span");
      box.type = "checkbox"; box.value = it.code; box.checked = anyProduct ? it.product : true;
      code.textContent = it.code; what.className = "muted small"; what.textContent = " " + it.what;
      lab.className = "chk"; lab.appendChild(box); lab.appendChild(code); lab.appendChild(what);
      li.appendChild(lab); ul.appendChild(li);
    });
    video.hidden = true; pick.hidden = false; msg.textContent = "";
  }

  function add(codes) {
    if (codes.length) fill(target, codes);
    dlg.close(); target.focus();
  }

  function fill(input, codes) {                     // add the new codes to what the field holds, comma-separated, without repeats
    var have = input.value.split(/[,;\s]+/).filter(Boolean);
    codes.forEach(function (c) { if (have.indexOf(c) < 0) have.push(c); });
    input.value = have.join(", ");
    input.dispatchEvent(new Event("input", { bubbles: true }));
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
    video.hidden = false; pick.hidden = true;
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
      msg.textContent = target ? "Point the camera at the product barcode (EAN / GTIN, or the one next to REF), not the one next to LOT, and hold it steady."
        : "Point the camera at a barcode and hold it steady, close enough to fill about a third of the view.";
      loop(me);
    }).catch(function (e) { if (!e || e.message !== "closed") msg.textContent = explain(e); });
  }

  function open(into) { target = into || null; dlg.showModal(); start(); }
  if (openBtn) openBtn.addEventListener("click", function () { open(null); });
  intoBtns.forEach(function (b) {
    b.addEventListener("click", function () { open(b.form && b.form.elements[b.getAttribute("data-scan-into")]); });
  });
  document.querySelectorAll(".headscan").forEach(function (a) {      // on Home the header button opens the camera here instead of reloading
    if (openBtn) a.addEventListener("click", function (e) { e.preventDefault(); open(null); });
  });
  dlg.querySelector(".scan-close").addEventListener("click", function () { dlg.close(); });
  pick.querySelector(".scan-add").addEventListener("click", function () {
    add(Array.prototype.map.call(pick.querySelectorAll("input:checked"), function (b) { return b.value; }));
  });
  pick.querySelector(".scan-again").addEventListener("click", start);
  dlg.addEventListener("close", stop);                                         // Esc, the Close button and navigating away all end here
  window.addEventListener("pagehide", stop);
  if (openBtn && /[?&]scan=1(&|$)/.test(window.location.search)) open(null);   // "Scan another" on the result page, or the header button elsewhere
})();

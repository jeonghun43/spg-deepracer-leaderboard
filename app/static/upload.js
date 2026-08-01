/*
 * 모델 업로드 진행 상황 표시 — specs/001-online-virtual-evaluation/upload-progress-ux.md
 *
 * 250MB 파일이 회선을 타고 가는 20초~수 분 동안 화면이 침묵하면 참가자는 서비스가
 * 고장 났다고 판단한다. 전송 시간 자체는 줄일 수 없으므로 "지금 무엇이 얼마나
 * 진행됐는지"를 계속 보여준다.
 *
 * 이 스크립트는 기존 form의 submit을 가로채는 방식으로만 동작한다. 파일이 로딩되지
 * 않거나 JS가 꺼져 있으면 form이 그대로 POST되어 지금까지와 똑같이 제출된다.
 * 대회 중에 참가자의 제출 경로가 이 파일 하나에 걸리게 두지 않기 위함이다.
 */
(function () {
  "use strict";

  var form = document.getElementById("submit-form");
  if (!form || !window.XMLHttpRequest || !window.FormData) return;

  var fileInput = document.getElementById("model_file");
  var submitButton = document.getElementById("submit-button");
  var cancelButton = document.getElementById("cancel-button");
  var progressBox = document.getElementById("upload-progress");
  var progressTrack = document.getElementById("progress-track");
  var progressBar = document.getElementById("progress-bar");
  var progressText = document.getElementById("progress-text");
  var errorBox = document.getElementById("upload-error");
  var fileInfo = document.getElementById("file-info");
  if (!fileInput || !submitButton || !progressBar || !progressText) return;

  var maxBytes = parseInt(form.getAttribute("data-max-bytes"), 10) || 0;
  var allowedExt = (form.getAttribute("data-allowed-ext") || "")
    .split(",")
    .map(function (ext) { return ext.trim().toLowerCase(); })
    .filter(Boolean);

  var xhr = null;
  var uploading = false;

  // ── 표시용 포맷 ────────────────────────────────────────────────────────────
  function formatBytes(bytes) {
    if (bytes >= 1024 * 1024 * 1024) return (bytes / (1024 * 1024 * 1024)).toFixed(1) + "GB";
    if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + "MB";
    if (bytes >= 1024) return (bytes / 1024).toFixed(0) + "KB";
    return bytes + "B";
  }

  function remainingLabel(seconds) {
    // 초 단위를 그대로 흘리면 숫자가 계속 튀어 오히려 불안해 보인다. 뭉뚱그린다.
    if (seconds < 5) return "거의 다 됐습니다";
    if (seconds < 60) return "약 " + (Math.ceil(seconds / 5) * 5) + "초 남음";
    if (seconds < 3600) return "약 " + Math.ceil(seconds / 60) + "분 남음";
    return "1시간 이상 남음";
  }

  // ── 상태 전환 ──────────────────────────────────────────────────────────────
  function showError(message) {
    if (!errorBox) return;
    errorBox.textContent = message;
    errorBox.hidden = false;
  }

  function clearError() {
    if (!errorBox) return;
    errorBox.textContent = "";
    errorBox.hidden = true;
  }

  function setProgress(percent) {
    progressBar.style.width = percent + "%";
    if (progressTrack) progressTrack.setAttribute("aria-valuenow", Math.round(percent));
  }

  function enterUploadingState() {
    uploading = true;
    submitButton.disabled = true;
    submitButton.textContent = "업로드 중…";
    if (cancelButton) cancelButton.hidden = false;
    if (progressBox) progressBox.hidden = false;
    fileInput.disabled = true;
    setProgress(0);
    progressText.textContent = "업로드를 시작하는 중…";
  }

  function leaveUploadingState() {
    uploading = false;
    submitButton.disabled = false;
    submitButton.textContent = "제출하기";
    if (cancelButton) cancelButton.hidden = true;
    if (progressBox) progressBox.hidden = true;
    // 파일 입력은 건드리지 않는다 — 고른 파일이 남아 있어야 버튼 한 번으로 재시도된다.
    fileInput.disabled = false;
    setProgress(0);
  }

  function fail(message) {
    leaveUploadingState();
    showError(message);
  }

  // ── 전송 전 검사 (전송을 시작하지도 않는다) ────────────────────────────────
  function validationError(file) {
    if (!file) return "업로드할 모델 파일을 선택해주세요.";
    var name = (file.name || "").toLowerCase();
    var extOk = allowedExt.length === 0 || allowedExt.some(function (ext) {
      return name.slice(-ext.length) === ext;
    });
    if (!extOk) return "허용되지 않는 파일 형식입니다 (허용: " + allowedExt.join(", ") + ").";
    if (file.size === 0) return "빈 파일은 업로드할 수 없습니다.";
    if (maxBytes && file.size > maxBytes) {
      return "파일 용량이 너무 큽니다 (최대 " + formatBytes(maxBytes) + "). 선택한 파일은 " +
        formatBytes(file.size) + "입니다.";
    }
    return null;
  }

  fileInput.addEventListener("change", function () {
    clearError();
    var file = fileInput.files && fileInput.files[0];
    if (!file) {
      if (fileInfo) fileInfo.hidden = true;
      return;
    }
    if (fileInfo) {
      fileInfo.textContent = "선택한 파일: " + file.name + " (" + formatBytes(file.size) + ")";
      fileInfo.hidden = false;
    }
    var problem = validationError(file);
    if (problem) showError(problem);
  });

  // ── 제출 ───────────────────────────────────────────────────────────────────
  form.addEventListener("submit", function (event) {
    if (uploading) {
      event.preventDefault();
      return;
    }
    var file = fileInput.files && fileInput.files[0];
    var problem = validationError(file);
    if (problem) {
      event.preventDefault();
      showError(problem);
      return;
    }

    event.preventDefault();
    clearError();
    enterUploadingState();

    var data = new FormData();
    data.append("model_file", file);

    var startedAt = Date.now();
    var lastAt = startedAt;
    var lastLoaded = 0;
    var rate = 0; // bytes/sec, 지수이동평균

    xhr = new XMLHttpRequest();
    xhr.open("POST", form.getAttribute("action") || "/submit", true);
    // 이 헤더를 보고 서버가 JSON으로 답한다. 없으면 기존 303 리다이렉트 경로다.
    xhr.setRequestHeader("Accept", "application/json");

    xhr.upload.onprogress = function (e) {
      if (!e.lengthComputable) {
        progressText.textContent = "업로드 중… (진행률을 알 수 없습니다)";
        return;
      }
      // 브라우저가 보고하는 loaded는 소켓 버퍼에 넘긴 양이라 실제 수신보다 앞선다.
      // 그래서 여기서는 100%를 쓰지 않고, 서버 응답을 기다리는 구간에서 따로 알린다.
      var percent = Math.min(99, (e.loaded / e.total) * 100);
      setProgress(percent);

      var now = Date.now();
      var elapsed = (now - lastAt) / 1000;
      if (elapsed >= 0.3) {
        var sample = (e.loaded - lastLoaded) / elapsed;
        rate = rate ? rate * 0.7 + sample * 0.3 : sample;
        lastAt = now;
        lastLoaded = e.loaded;
      }

      var text = Math.floor(percent) + "%  ·  " + formatBytes(e.loaded) + " / " + formatBytes(e.total);
      // 시작 직후의 표본은 심하게 튄다("약 4시간 남음"). 1초는 지나고 나서 보여준다.
      if (rate > 0 && now - startedAt > 1000) {
        text += "  ·  " + formatBytes(rate) + "/s  ·  " + remainingLabel((e.total - e.loaded) / rate);
      }
      progressText.textContent = text;
    };

    xhr.upload.onload = function () {
      // 바이트는 다 넘겼지만 서버는 아직 파일을 받아 검사하는 중이다. 이 구간을
      // 100%로 놔두면 "막대가 멈췄는데 화면이 안 넘어간다"는 새로운 불안이 생긴다.
      setProgress(100);
      progressText.textContent = "서버에서 파일을 확인하는 중…";
    };

    xhr.onload = function () {
      var payload = null;
      try {
        payload = JSON.parse(xhr.responseText);
      } catch (err) {
        payload = null;
      }

      if (xhr.status >= 200 && xhr.status < 300 && payload && payload.ok) {
        uploading = false; // 이탈 경고를 끄고 이동한다
        progressText.textContent = "제출이 접수되었습니다. 화면을 이동합니다…";
        window.location.href = payload.redirect || "/submit";
        return;
      }
      if (payload && payload.error) {
        fail(payload.error);
        return;
      }
      // JSON이 아니면 대개 로그인 화면(세션 만료)으로 리다이렉트된 경우다.
      fail("제출에 실패했습니다. 로그인이 만료되었을 수 있으니 페이지를 새로고침한 뒤 다시 시도해주세요.");
    };

    xhr.onerror = function () {
      fail("네트워크 연결이 끊겨 업로드에 실패했습니다. 연결을 확인한 뒤 다시 시도해주세요.");
    };

    xhr.ontimeout = function () {
      fail("업로드 시간이 초과되었습니다. 다시 시도해주세요.");
    };

    xhr.onabort = function () {
      leaveUploadingState();
      showError("업로드를 취소했습니다. 파일은 그대로 선택되어 있으니 다시 제출할 수 있습니다.");
    };

    xhr.send(data);
  });

  if (cancelButton) {
    cancelButton.addEventListener("click", function () {
      if (xhr && uploading) xhr.abort();
    });
  }

  // 업로드 중 새로고침·뒤로가기 한 번에 수 분치 전송이 날아가는 것을 막는다.
  window.addEventListener("beforeunload", function (event) {
    if (!uploading) return;
    event.preventDefault();
    event.returnValue = "";
    return "";
  });
})();

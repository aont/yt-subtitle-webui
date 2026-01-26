const statusEl = document.getElementById("status");
const logArea = document.getElementById("logArea");
const downloadBtn = document.getElementById("downloadBtn");
const copyBtn = document.getElementById("copyBtn");
const videoIdInput = document.getElementById("videoId");
const languageEl = document.getElementById("language");
const subtitleStart = document.getElementById("subtitleStart");
const subtitleEnd = document.getElementById("subtitleEnd");

let socket;
let fullSubtitle = "";

const appendLog = (line) => {
  logArea.textContent += `${line}\n`;
  logArea.scrollTop = logArea.scrollHeight;
};

const setStatus = (message) => {
  statusEl.textContent = `Status: ${message}`;
};

const setPreview = (text) => {
  const normalized = text.replace(/\s+/g, " ").trim();
  const length = normalized.length;
  const previewSize = 400;
  subtitleStart.textContent = normalized.slice(0, previewSize) || "-";
  subtitleEnd.textContent =
    length > previewSize
      ? normalized.slice(Math.max(0, length - previewSize))
      : normalized || "-";
};

const connectSocket = () => {
  if (socket && socket.readyState === WebSocket.OPEN) {
    return;
  }
  socket = new WebSocket("ws://localhost:8765");

  socket.addEventListener("open", () => {
    appendLog("WebSocket connected.");
  });

  socket.addEventListener("message", (event) => {
    const payload = JSON.parse(event.data);
    if (payload.type === "log") {
      appendLog(payload.message);
    }
    if (payload.type === "error") {
      appendLog(`Error: ${payload.message}`);
      setStatus("Error");
      downloadBtn.disabled = false;
    }
    if (payload.type === "result") {
      fullSubtitle = payload.text;
      languageEl.textContent = payload.language || "unknown";
      setPreview(fullSubtitle);
      copyBtn.disabled = false;
      setStatus("Complete");
      downloadBtn.disabled = false;
      appendLog("Subtitle ready.");
    }
  });

  socket.addEventListener("close", () => {
    appendLog("WebSocket disconnected.");
  });
};

const copyText = async (text) => {
  try {
    await navigator.clipboard.writeText(text);
    appendLog("Copied subtitle to clipboard (Clipboard API). ");
    return true;
  } catch (error) {
    appendLog("Clipboard API failed, trying execCommand...");
  }

  const helper = document.createElement("textarea");
  helper.value = text;
  document.body.appendChild(helper);
  helper.select();
  try {
    const success = document.execCommand("copy");
    document.body.removeChild(helper);
    if (success) {
      appendLog("Copied subtitle to clipboard (execCommand). ");
      return true;
    }
  } catch (error) {
    appendLog("execCommand copy failed.");
  }
  document.body.removeChild(helper);
  return false;
};

copyBtn.addEventListener("click", async () => {
  if (!fullSubtitle) {
    return;
  }
  await copyText(fullSubtitle);
});

downloadBtn.addEventListener("click", () => {
  const videoId = videoIdInput.value.trim();
  if (!videoId) {
    setStatus("Video ID required");
    return;
  }
  logArea.textContent = "";
  subtitleStart.textContent = "-";
  subtitleEnd.textContent = "-";
  languageEl.textContent = "-";
  fullSubtitle = "";
  copyBtn.disabled = true;

  connectSocket();
  if (socket.readyState !== WebSocket.OPEN) {
    socket.addEventListener(
      "open",
      () => {
        socket.send(JSON.stringify({ type: "download", id: videoId }));
      },
      { once: true }
    );
  } else {
    socket.send(JSON.stringify({ type: "download", id: videoId }));
  }

  downloadBtn.disabled = true;
  setStatus("Downloading...");
});

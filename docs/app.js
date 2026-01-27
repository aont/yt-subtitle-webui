const statusEl = document.getElementById("status");
const backendAddressInput = document.getElementById("backendAddress");
const watchIdInput = document.getElementById("watchId");
const downloadButton = document.getElementById("downloadButton");
const copyButton = document.getElementById("copyButton");
const summaryBeginning = document.getElementById("summaryBeginning");
const summaryEnding = document.getElementById("summaryEnding");
const fullText = document.getElementById("fullText");
const logEl = document.getElementById("log");

let socket;
let latestText = "";

function appendLog(message, level = "info") {
  const entry = document.createElement("div");
  entry.className = `log-entry ${level}`;
  const timestamp = new Date().toLocaleTimeString();
  entry.textContent = `[${timestamp}] ${message}`;
  logEl.appendChild(entry);
  logEl.scrollTop = logEl.scrollHeight;
}

function setStatus(message) {
  statusEl.textContent = `Status: ${message}`;
  appendLog(`Frontend: ${message}`);
}

function defaultSocketUrl() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.hostname}:${window.location.port || 8080}/ws`;
}

function normalizeSocketUrl(value) {
  const trimmed = value.trim();
  if (!trimmed) {
    return defaultSocketUrl();
  }
  try {
    let url;
    if (/^wss?:\/\//i.test(trimmed)) {
      url = new URL(trimmed);
    } else if (/^https?:\/\//i.test(trimmed)) {
      url = new URL(trimmed);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    } else {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      url = new URL(`${protocol}//${trimmed}`);
    }

    if (!url.pathname || url.pathname === "/") {
      url.pathname = "/ws";
    } else if (!url.pathname.endsWith("/ws")) {
      url.pathname = `${url.pathname.replace(/\/+$/, "")}/ws`;
    }
    return url.toString();
  } catch (error) {
    appendLog("Frontend: Invalid backend address provided, using default.", "warn");
    return defaultSocketUrl();
  }
}

function connectSocket() {
  if (socket && socket.readyState === WebSocket.OPEN) {
    return socket;
  }

  const socketUrl = normalizeSocketUrl(backendAddressInput?.value || "");
  socket = new WebSocket(socketUrl);

  socket.addEventListener("open", () => {
    appendLog("Frontend: WebSocket connection established.");
  });

  socket.addEventListener("message", (event) => {
    let data;
    try {
      data = JSON.parse(event.data);
    } catch (error) {
      appendLog("Frontend: Received non-JSON message.", "error");
      return;
    }

    if (data.type === "log") {
      appendLog(`Backend: ${data.message}`);
      return;
    }

    if (data.type === "error") {
      appendLog(`Backend error: ${data.message}`, "error");
      setStatus("Error");
      downloadButton.disabled = false;
      return;
    }

    if (data.type === "result") {
      latestText = data.text || "";
      summaryBeginning.textContent = data.summary?.beginning || "";
      summaryEnding.textContent = data.summary?.ending || "";
      fullText.value = latestText;
      copyButton.disabled = !latestText;
      setStatus(`Completed (language: ${data.language || "unknown"})`);
      downloadButton.disabled = false;
      appendLog("Frontend: Results loaded into the UI.");
    }
  });

  socket.addEventListener("close", () => {
    appendLog("Frontend: WebSocket connection closed.", "warn");
  });

  socket.addEventListener("error", () => {
    appendLog("Frontend: WebSocket error.", "error");
  });

  return socket;
}

function sanitizeWatchId(value) {
  const trimmed = value.trim();
  if (!trimmed) {
    return "";
  }
  return trimmed;
}

async function copyToClipboard(text) {
  if (!text) {
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    appendLog("Frontend: Copied subtitles using Clipboard API.");
    setStatus("Copied to clipboard");
    return;
  } catch (error) {
    appendLog("Frontend: Clipboard API failed, falling back to execCommand.", "warn");
  }

  const temp = document.createElement("textarea");
  temp.value = text;
  document.body.appendChild(temp);
  temp.select();
  try {
    document.execCommand("copy");
    appendLog("Frontend: Copied subtitles using execCommand.");
    setStatus("Copied to clipboard");
  } catch (error) {
    appendLog("Frontend: Failed to copy subtitles.", "error");
    setStatus("Copy failed");
  } finally {
    document.body.removeChild(temp);
  }
}

downloadButton.addEventListener("click", () => {
  const watchId = sanitizeWatchId(watchIdInput.value);
  if (!watchId) {
    setStatus("Please enter a watch ID or URL");
    return;
  }

  summaryBeginning.textContent = "Downloading...";
  summaryEnding.textContent = "Downloading...";
  fullText.value = "";
  latestText = "";
  copyButton.disabled = true;
  downloadButton.disabled = true;

  setStatus("Requesting subtitles...");
  const ws = connectSocket();
  ws.addEventListener(
    "open",
    () => {
      ws.send(
        JSON.stringify({
          action: "download",
          watch_id: watchId,
        })
      );
      appendLog(`Frontend: Sent download request for ${watchId}.`);
    },
    { once: true }
  );

  if (ws.readyState === WebSocket.OPEN) {
    ws.send(
      JSON.stringify({
        action: "download",
        watch_id: watchId,
      })
    );
    appendLog(`Frontend: Sent download request for ${watchId}.`);
  }
});

copyButton.addEventListener("click", () => copyToClipboard(latestText));

if (backendAddressInput) {
  const saved = window.localStorage.getItem("backendAddress");
  if (saved) {
    backendAddressInput.value = saved;
  }
  backendAddressInput.addEventListener("change", () => {
    const value = backendAddressInput.value.trim();
    if (value) {
      window.localStorage.setItem("backendAddress", value);
    } else {
      window.localStorage.removeItem("backendAddress");
    }
  });
}

setStatus("Idle");

const statusEl = document.getElementById("status");
const backendAddressInput = document.getElementById("backendAddress");
const watchIdInput = document.getElementById("watchId");
const downloadButton = document.getElementById("downloadButton");
const copyButton = document.getElementById("copyButton");
const copyPromptButton = document.getElementById("copyPromptButton");
const promptTemplateInput = document.getElementById("promptTemplate");
const fullText = document.getElementById("fullText");
const logEl = document.getElementById("log");
const completionModal = document.getElementById("completionModal");
const modalTitle = document.getElementById("modalTitle");
const modalMessage = document.getElementById("modalMessage");
const modalCloseButton = document.getElementById("modalCloseButton");
const modalDismissButton = document.getElementById("modalDismissButton");

let socket;
let latestText = "";
const defaultPromptTemplate =
  "Turn the following audio transcription into a blog post in English.\n----\n\n{{URL}}\n\n";

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

function setModalState(isOpen, tone) {
  if (!completionModal) {
    return;
  }
  completionModal.classList.remove("success", "error");
  if (tone) {
    completionModal.classList.add(tone);
  }
  completionModal.classList.toggle("is-visible", isOpen);
  completionModal.setAttribute("aria-hidden", String(!isOpen));
}

function showCompletionModal({ title, message, tone }) {
  if (!completionModal || !modalTitle || !modalMessage) {
    return;
  }
  modalTitle.textContent = title;
  modalMessage.textContent = message;
  setModalState(true, tone);
}

function hideCompletionModal() {
  setModalState(false);
}

function defaultSocketUrl() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.hostname}:${window.location.port || 8080}/ws`;
}

function defaultBackendAddress() {
  return defaultSocketUrl();
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
      showCompletionModal({
        title: "Download failed",
        message: data.message || "The backend reported an error while processing the request.",
        tone: "error",
      });
      return;
    }

    if (data.type === "result") {
      latestText = data.text || "";
      fullText.value = latestText;
      copyButton.disabled = !latestText;
      if (copyPromptButton) {
        copyPromptButton.disabled = !latestText;
      }
      setStatus(`Completed (language: ${data.language || "unknown"})`);
      downloadButton.disabled = false;
      appendLog("Frontend: Results loaded into the UI.");
      showCompletionModal({
        title: "Download complete",
        message: `Subtitles are ready${
          data.language ? ` in ${data.language}` : ""
        }. You can copy the text or the prompt from the buttons above.`,
        tone: "success",
      });
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

function buildYouTubeUrl(value) {
  if (!value) {
    return "";
  }
  if (/^https?:\/\//i.test(value)) {
    return value;
  }
  return `https://www.youtube.com/watch?v=${value}`;
}

async function copyToClipboard(text, label = "subtitles") {
  if (!text) {
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    appendLog(`Frontend: Copied ${label} using Clipboard API.`);
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
    appendLog(`Frontend: Copied ${label} using execCommand.`);
    setStatus("Copied to clipboard");
  } catch (error) {
    appendLog(`Frontend: Failed to copy ${label}.`, "error");
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

  fullText.value = "";
  latestText = "";
  copyButton.disabled = true;
  if (copyPromptButton) {
    copyPromptButton.disabled = true;
  }
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

copyButton.addEventListener("click", () => copyToClipboard(latestText, "subtitles"));
if (copyPromptButton) {
  copyPromptButton.addEventListener("click", () => {
    const template = promptTemplateInput?.value ?? defaultPromptTemplate;
    const watchId = sanitizeWatchId(watchIdInput.value);
    const url = buildYouTubeUrl(watchId);
    const filledTemplate = template.replaceAll("{{URL}}", url);
    const combined = `${filledTemplate}${latestText}`;
    copyToClipboard(combined, "prompt");
  });
}

if (backendAddressInput) {
  const saved = window.localStorage.getItem("backendAddress");
  if (saved) {
    backendAddressInput.value = saved;
  } else {
    backendAddressInput.value = defaultBackendAddress();
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

if (promptTemplateInput) {
  const savedTemplate = window.localStorage.getItem("promptTemplate");
  if (savedTemplate) {
    promptTemplateInput.value = savedTemplate;
  } else {
    promptTemplateInput.value = defaultPromptTemplate;
    window.localStorage.setItem("promptTemplate", defaultPromptTemplate);
  }
  promptTemplateInput.addEventListener("input", () => {
    window.localStorage.setItem("promptTemplate", promptTemplateInput.value);
  });
}

setStatus("Idle");

if (completionModal) {
  completionModal.addEventListener("click", (event) => {
    if (event.target === completionModal) {
      hideCompletionModal();
    }
  });
}

if (modalCloseButton) {
  modalCloseButton.addEventListener("click", hideCompletionModal);
}

if (modalDismissButton) {
  modalDismissButton.addEventListener("click", hideCompletionModal);
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && completionModal?.classList.contains("is-visible")) {
    hideCompletionModal();
  }
});

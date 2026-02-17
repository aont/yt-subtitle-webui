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

function defaultBackendAddress() {
  return window.location.origin;
}

function normalizeBackendBaseUrl(value) {
  const trimmed = value.trim();
  if (!trimmed) {
    return defaultBackendAddress();
  }

  try {
    if (/^https?:\/\//i.test(trimmed)) {
      const url = new URL(trimmed);
      return url.toString().replace(/\/$/, "");
    }

    const protocol = window.location.protocol === "https:" ? "https:" : "http:";
    const url = new URL(`${protocol}//${trimmed}`);
    return url.toString().replace(/\/$/, "");
  } catch (error) {
    appendLog("Frontend: Invalid backend address provided, using default.", "warn");
    return defaultBackendAddress();
  }
}

function buildEndpointUrl(baseUrl, endpoint) {
  const cleaned = endpoint.replace(/^\/+/, "");
  return `${baseUrl}/${cleaned}`;
}

async function createReservation(baseUrl, watchId) {
  const response = await fetch(buildEndpointUrl(baseUrl, "reservation"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ watch_id: watchId }),
  });

  let payload = {};
  try {
    payload = await response.json();
  } catch (error) {
    payload = {};
  }

  if (!response.ok) {
    throw new Error(payload.message || "Failed to create reservation.");
  }

  if (!payload.reservation_id) {
    throw new Error("Backend did not return a reservation ID.");
  }

  return payload.reservation_id;
}

function waitForEvents(baseUrl, reservationId) {
  return new Promise((resolve, reject) => {
    const eventsUrl = buildEndpointUrl(baseUrl, `events/${encodeURIComponent(reservationId)}`);
    const eventSource = new EventSource(eventsUrl);

    eventSource.addEventListener("open", () => {
      appendLog("Frontend: Event stream connected.");
    });

    eventSource.addEventListener("log", (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch (error) {
        appendLog("Frontend: Received invalid log event payload.", "warn");
        return;
      }
      appendLog(`Backend: ${data.message || ""}`);
    });

    eventSource.addEventListener("error", (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch (error) {
        data = {};
      }
      eventSource.close();
      reject(new Error(data.message || "Stream error from backend."));
    });

    eventSource.addEventListener("completed", () => {
      eventSource.close();
      resolve();
    });

    eventSource.onerror = () => {
      eventSource.close();
      reject(new Error("Failed to connect to event stream."));
    };
  });
}

async function fetchResult(baseUrl, reservationId) {
  const response = await fetch(buildEndpointUrl(baseUrl, `result/${encodeURIComponent(reservationId)}`));
  let payload = {};
  try {
    payload = await response.json();
  } catch (error) {
    payload = {};
  }

  if (!response.ok) {
    throw new Error(payload.message || "Failed to fetch result.");
  }

  return payload;
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

downloadButton.addEventListener("click", async () => {
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

  const baseUrl = normalizeBackendBaseUrl(backendAddressInput?.value || "");

  try {
    setStatus("Creating reservation...");
    const reservationId = await createReservation(baseUrl, watchId);
    appendLog(`Frontend: Reservation created (${reservationId}).`);

    setStatus("Waiting for backend events...");
    await waitForEvents(baseUrl, reservationId);

    setStatus("Fetching final subtitle result...");
    const data = await fetchResult(baseUrl, reservationId);

    latestText = data.text || "";
    fullText.value = latestText;
    copyButton.disabled = !latestText;
    if (copyPromptButton) {
      copyPromptButton.disabled = !latestText;
    }
    setStatus(`Completed (language: ${data.language || "unknown"})`);
    appendLog("Frontend: Results loaded into the UI.");
    showCompletionModal({
      title: "Download complete",
      message: `Subtitles are ready${
        data.language ? ` in ${data.language}` : ""
      }. You can copy the text or the prompt from the buttons above.`,
      tone: "success",
    });
  } catch (error) {
    appendLog(`Backend error: ${error.message}`, "error");
    setStatus("Error");
    showCompletionModal({
      title: "Download failed",
      message: error.message || "The backend reported an error while processing the request.",
      tone: "error",
    });
  } finally {
    downloadButton.disabled = false;
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

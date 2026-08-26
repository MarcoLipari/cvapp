const $ = (selector) => document.querySelector(selector);
const browserApi = globalThis.browser ?? globalThis.chrome;

function setStatus(message, state = "") {
  const status = $("#status");
  status.textContent = message;
  status.className = state;
}

async function activeTab() {
  const tabs = await browserApi.tabs.query({ active: true, currentWindow: true });
  return tabs[0];
}

async function loadSettings() {
  const settings = await browserApi.storage.local.get(["endpoint", "token"]);
  $("#endpoint").value = settings.endpoint || "";
  $("#token").value = settings.token || "";
}

$("#save-settings").addEventListener("click", async () => {
  await browserApi.storage.local.set({ endpoint: $("#endpoint").value.trim(), token: $("#token").value.trim() });
  setStatus("CV Manager connection saved.", "success");
});

$("#capture-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const endpoint = $("#endpoint").value.trim();
  const token = $("#token").value.trim();
  if (!endpoint || !token) {
    setStatus("Copy the endpoint and token from Safari Capture first.", "error");
    return;
  }
  const button = event.submitter;
  button.disabled = true;
  setStatus("Saving…");
  const payload = {
    company: $("#company").value.trim(),
    role: $("#role").value.trim(),
    location: $("#location").value.trim(),
    posting_url: $("#posting-url").value.trim(),
    notes: $("#notes").value.trim()
  };
  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CV-Manager-Token": token },
      body: JSON.stringify(payload)
    });
    if (!response.ok) throw new Error((await response.json()).error || `Request failed (${response.status})`);
    setStatus("Saved to CV Manager.", "success");
    await browserApi.storage.local.set({ endpoint, token });
  } catch (error) {
    setStatus(error.message === "Failed to fetch" ? "CV Manager is not listening. Start Safari Capture in the app." : error.message, "error");
  } finally {
    button.disabled = false;
  }
});

activeTab().then((tab) => { $("#posting-url").value = tab?.url || ""; });
loadSettings();

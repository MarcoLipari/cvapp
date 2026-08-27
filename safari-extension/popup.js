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

async function send(message) {
  if (globalThis.browser) return browserApi.runtime.sendMessage(message);
  return new Promise((resolve, reject) => {
    browserApi.runtime.sendMessage(message, (response) => {
      const error = browserApi.runtime.lastError;
      if (error) reject(new Error(error.message)); else resolve(response);
    });
  });
}

async function loadCvs() {
  const list = $("#cv-list");
  try {
    const response = await send({ type: "listCvs" });
    if (!response?.ok) throw new Error(response?.error || "Native helper unavailable.");
    list.textContent = "";
    if (!response.cvs?.length) {
      const empty = document.createElement("p"); empty.textContent = "No exported CVs are available yet."; list.appendChild(empty);
      return;
    }
    for (const cv of response.cvs) {
      const button = document.createElement("button");
      button.type = "button"; button.className = "cv-option"; button.textContent = cv.name;
      button.addEventListener("click", async () => {
        button.disabled = true; setStatus("Attaching…");
        try {
          const tab = await activeTab();
          const result = await send({ type: "attachCv", tabId: tab.id, cvId: cv.id });
          if (!result?.ok) throw new Error(result?.error || "Could not attach that CV.");
          setStatus(`${cv.name} attached.`, "success");
        } catch (error) {
          setStatus(error.message, "error");
        } finally {
          button.disabled = false;
        }
      });
      list.appendChild(button);
    }
    setStatus("Native bridge connected.", "success");
  } catch (error) {
    list.textContent = "";
    const help = document.createElement("p"); help.textContent = "Open the CV Manager Safari host once, then enable its extension in Safari Settings."; list.appendChild(help);
    setStatus(error.message, "error");
  }
}

$("#log-page").addEventListener("click", async () => {
  const button = $("#log-page"); button.disabled = true; setStatus("Logging…");
  try {
    const tab = await activeTab();
    const response = await browserApi.tabs.sendMessage(tab.id, { type: "manualLog" });
    if (!response?.ok) throw new Error(response?.error || "Could not log this page.");
    setStatus("Application logged.", "success");
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    button.disabled = false;
  }
});

loadCvs();

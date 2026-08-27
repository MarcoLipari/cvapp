const browserApi = globalThis.browser ?? globalThis.chrome;
const nativeApplication = "com.cvmanager.app.safari";

function identifier() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function localDate() {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

async function nativeMessage(message) {
  let response;
  if (globalThis.browser) {
    response = await browserApi.runtime.sendNativeMessage(nativeApplication, message);
  } else {
    response = await new Promise((resolve, reject) => {
      browserApi.runtime.sendNativeMessage(nativeApplication, message, (value) => {
        const error = browserApi.runtime.lastError;
        if (error) reject(new Error(error.message)); else resolve(value);
      });
    });
  }
  if (!response?.ok) throw new Error(response?.error || "CV Manager's native Safari helper is unavailable.");
  return response;
}

function candidateKey(tabId) {
  return `application-candidate:${tabId}`;
}

async function readCandidate(tabId) {
  if (tabId == null) return {};
  const key = candidateKey(tabId);
  const values = await browserApi.storage.local.get(key);
  return values[key] || {};
}

async function writeCandidate(tabId, candidate) {
  if (tabId == null) return;
  await browserApi.storage.local.set({ [candidateKey(tabId)]: candidate });
}

async function clearCandidate(tabId) {
  if (tabId != null) await browserApi.storage.local.remove(candidateKey(tabId));
}

function mergeCandidate(previous, next) {
  return {
    ...previous,
    ...next,
    snapshot: { ...(previous.snapshot || {}), ...(next.snapshot || {}) }
  };
}

async function persistEvent(event) {
  await nativeMessage({ operation: "write_event", request: {
    version: 1,
    request_id: identifier(),
    event_id: event.event_id,
    revision: event.revision,
    state: event.state,
    payload: event.payload
  } });
  return event;
}

async function handleMessage(message, sender) {
  const tabId = message.tabId ?? sender.tab?.id;
  switch (message.type) {
    case "ping":
      return nativeMessage({ operation: "ping" });
    case "listCvs":
      return nativeMessage({ operation: "list_cvs" });
    case "getCv":
      return nativeMessage({ operation: "get_cv", cv_id: Number(message.cvId) });
    case "rememberCandidate": {
      const candidate = mergeCandidate(await readCandidate(tabId), message.candidate || {});
      await writeCandidate(tabId, candidate);
      return { ok: true, candidate };
    }
    case "getCandidate":
      return { ok: true, candidate: await readCandidate(tabId) };
    case "logDetected": {
      const candidate = mergeCandidate(await readCandidate(tabId), message.candidate || {});
      const event = {
        event_id: identifier(),
        revision: 1,
        state: "active",
        payload: {
          company: candidate.company || "Unknown company",
          role: candidate.role || "Unknown role",
          location: candidate.location || "",
          posting_url: candidate.posting_url || sender.tab?.url || "",
          application_date: localDate(),
          cv_id: candidate.cv_id ?? null,
          cv_name: candidate.cv_name || "",
          notes: candidate.notes || "",
          snapshot: candidate.snapshot || {}
        }
      };
      await persistEvent(event);
      await clearCandidate(tabId);
      return { ok: true, event };
    }
    case "writeEvent":
      await persistEvent(message.event);
      return { ok: true, event: message.event };
    case "attachCv": {
      const cv = await nativeMessage({ operation: "get_cv", cv_id: Number(message.cvId) });
      await browserApi.tabs.sendMessage(tabId, { type: "attachCvData", cv });
      return { ok: true };
    }
    default:
      throw new Error("Unknown extension message");
  }
}

browserApi.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const work = handleMessage(message, sender).catch((error) => ({ ok: false, error: error.message }));
  if (globalThis.browser) return work;
  work.then(sendResponse);
  return true;
});

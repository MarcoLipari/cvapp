(() => {
  const browserApi = globalThis.browser ?? globalThis.chrome;
  const decoratedInputs = new WeakSet();
  let confirmationTimer;
  let activeToast;

  async function send(message) {
    if (globalThis.browser) return browserApi.runtime.sendMessage(message);
    return new Promise((resolve, reject) => {
      browserApi.runtime.sendMessage(message, (response) => {
        const error = browserApi.runtime.lastError;
        if (error) reject(new Error(error.message)); else resolve(response);
      });
    });
  }

  function plainText(value) {
    const element = document.createElement("div");
    element.innerHTML = String(value || "");
    return (element.textContent || "").replace(/\s+/g, " ").trim();
  }

  function jobPosting() {
    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const documentValue = JSON.parse(script.textContent);
        const values = Array.isArray(documentValue) ? documentValue : [documentValue];
        for (const value of values.flatMap((item) => item?.["@graph"] || [item])) {
          const types = Array.isArray(value?.["@type"]) ? value["@type"] : [value?.["@type"]];
          if (types.includes("JobPosting")) return value;
        }
      } catch (_error) {
        // Ignore malformed page metadata and use visible-page fallbacks.
      }
    }
    return {};
  }

  function locationText(posting) {
    const locations = Array.isArray(posting.jobLocation) ? posting.jobLocation : [posting.jobLocation];
    return locations.filter(Boolean).map((item) => {
      const address = item.address || item;
      return [address.addressLocality, address.addressRegion, address.addressCountry].filter(Boolean).join(", ");
    }).filter(Boolean).join(" · ");
  }

  function visibleText(selectors) {
    for (const selector of selectors) {
      const value = document.querySelector(selector)?.textContent?.replace(/\s+/g, " ").trim();
      if (value) return value;
    }
    return "";
  }

  function extractCandidate() {
    const posting = jobPosting();
    const canonical = document.querySelector('link[rel="canonical"]')?.href || location.href;
    const role = plainText(posting.title) || visibleText(["h1", "[data-job-title]", "[class*='job-title']"]);
    const company = plainText(posting.hiringOrganization?.name) || visibleText([
      "[data-company-name]", "[class*='company-name']", "[class*='companyName']", "[itemprop='hiringOrganization']"
    ]) || location.hostname.replace(/^www\./, "");
    const descriptionElement = document.querySelector("[data-job-description], [class*='job-description'], [class*='jobDescription'], main");
    const description = plainText(posting.description) || descriptionElement?.innerText?.replace(/\s+/g, " ").trim() || "";
    const identifier = typeof posting.identifier === "object" ? posting.identifier?.value : posting.identifier;
    return {
      company,
      role: role || document.title.split(/[|–—-]/)[0].trim() || "Unknown role",
      location: locationText(posting) || visibleText(["[data-job-location]", "[class*='job-location']", "[class*='location']"]),
      posting_url: canonical,
      snapshot: {
        description: description.slice(0, 100_000),
        external_job_id: String(identifier || ""),
        source_site: location.hostname,
        page_title: document.title,
        captured_url: location.href
      }
    };
  }

  function decodeBase64(value) {
    const binary = atob(value);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    return bytes;
  }

  function safeFilename(name) {
    const stem = String(name || "CV")
      .replace(/\.pdf$/i, "")
      .replace(/[^a-z0-9 _.-]/gi, "")
      .trim() || "CV";
    return `${stem}.pdf`;
  }

  async function rememberCv(cv) {
    await send({ type: "rememberCandidate", candidate: { ...extractCandidate(), cv_id: cv.id, cv_name: cv.name } });
  }

  async function attachCv(input, cv) {
    const file = new File([decodeBase64(cv.data)], safeFilename(cv.upload_filename || cv.name), { type: "application/pdf" });
    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    if (input.files?.length !== 1) throw new Error("This site did not accept the CV automatically.");
    await rememberCv(cv);
    const button = input.parentElement?.querySelector(".cv-manager-picker-button");
    if (button) button.textContent = `✓ ${cv.name}`;
  }

  function removeChooser() {
    document.querySelector(".cv-manager-chooser")?.remove();
  }

  async function showChooser(input) {
    removeChooser();
    const chooser = document.createElement("div");
    chooser.className = "cv-manager-chooser";
    const heading = document.createElement("strong");
    heading.textContent = "Choose a CV";
    const close = document.createElement("button");
    close.className = "cv-manager-close"; close.type = "button"; close.textContent = "×"; close.addEventListener("click", removeChooser);
    const list = document.createElement("div");
    list.className = "cv-manager-chooser-list"; list.textContent = "Loading…";
    chooser.append(heading, close, list); document.documentElement.appendChild(chooser);
    try {
      const response = await send({ type: "listCvs" });
      if (!response?.ok) throw new Error(response?.error || "Could not load CVs.");
      list.textContent = "";
      if (!response.cvs?.length) list.textContent = "Export a CV in CV Manager first.";
      for (const item of response.cvs || []) {
        const option = document.createElement("button");
        option.type = "button"; option.className = "cv-manager-cv-option";
        option.textContent = item.name;
        option.addEventListener("click", async () => {
          option.disabled = true; option.textContent = "Attaching…";
          try {
            const full = await send({ type: "getCv", cvId: item.id });
            if (!full?.ok) throw new Error(full?.error || "Could not read that CV.");
            await attachCv(input, full);
            removeChooser();
          } catch (error) {
            option.disabled = false; option.textContent = error.message;
          }
        });
        list.appendChild(option);
      }
    } catch (error) {
      list.textContent = error.message;
    }
  }

  async function matchManualUpload(input) {
    const file = input.files?.[0];
    if (!file) return;
    try {
      const digest = [...new Uint8Array(await crypto.subtle.digest("SHA-256", await file.arrayBuffer()))]
        .map((byte) => byte.toString(16).padStart(2, "0")).join("");
      const response = await send({ type: "listCvs" });
      const match = response?.cvs?.find((cv) => cv.sha256 === digest);
      if (match) await rememberCv(match);
      else await send({ type: "rememberCandidate", candidate: extractCandidate() });
    } catch (_error) {
      // A failed optional match must not interfere with the site's upload.
    }
  }

  function decorateInputs() {
    for (const input of document.querySelectorAll('input[type="file"]')) {
      if (decoratedInputs.has(input)) continue;
      const acceptsDocument = !input.accept || /pdf|document|doc|resume|cv/i.test(input.accept + input.name + input.id);
      if (!acceptsDocument) continue;
      decoratedInputs.add(input);
      const button = document.createElement("button");
      button.type = "button"; button.className = "cv-manager-picker-button"; button.textContent = "Choose from CV Manager";
      button.addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); showChooser(input); });
      input.insertAdjacentElement("afterend", button);
      input.addEventListener("change", () => matchManualUpload(input));
    }
  }

  function submissionText(target) {
    const button = target?.closest?.('button, input[type="submit"], [role="button"]');
    return `${button?.textContent || ""} ${button?.value || ""} ${button?.getAttribute?.("aria-label") || ""}`.replace(/\s+/g, " ").trim();
  }

  function looksFinal(text, form) {
    if (/submit (your )?application|send (your )?application|complete application|finish application/i.test(text)) return true;
    return /^submit$/i.test(text) && Boolean(form?.querySelector('input[type="file"], input[name*="resume" i], input[name*="cv" i]'));
  }

  async function armSubmission() {
    await send({ type: "rememberCandidate", candidate: { ...extractCandidate(), armed_at: Date.now() } });
    scheduleConfirmationCheck(500);
  }

  function confirmationVisible() {
    const text = document.body?.innerText?.replace(/\s+/g, " ").slice(0, 80_000) || "";
    return /application (?:has been )?submitted|application received|thank you for (?:your application|applying)|successfully (?:submitted|applied)|we(?:'|’)ve received your application/i.test(text)
      || /(?:application-)?(?:confirmation|submitted|thank-you)/i.test(location.pathname);
  }

  function scheduleConfirmationCheck(delay = 800) {
    clearTimeout(confirmationTimer);
    confirmationTimer = setTimeout(checkConfirmation, delay);
  }

  async function checkConfirmation() {
    if (!confirmationVisible()) return;
    const remembered = await send({ type: "getCandidate" });
    const armedAt = Number(remembered?.candidate?.armed_at || 0);
    if (!armedAt || Date.now() - armedAt > 2 * 60 * 60 * 1000) return;
    const response = await send({ type: "logDetected", candidate: extractCandidate() });
    if (response?.ok) showLoggedToast(response.event);
    else showMessageToast(response?.error || "Could not log this application.", true);
  }

  function toastShell() {
    activeToast?.remove();
    const toast = document.createElement("aside");
    toast.className = "cv-manager-toast";
    activeToast = toast; document.documentElement.appendChild(toast);
    return toast;
  }

  function showMessageToast(message, error = false) {
    const toast = toastShell();
    if (error) toast.classList.add("cv-manager-error");
    const title = document.createElement("strong"); title.textContent = error ? "CV Manager" : "Job logged";
    const body = document.createElement("span"); body.textContent = message;
    toast.append(title, body); setTimeout(() => toast.remove(), 8000);
  }

  function eventSummary(event) {
    const payload = event.payload;
    return `${payload.role} · ${payload.company} · ${payload.application_date}${payload.cv_name ? ` · ${payload.cv_name}` : " · CV unknown"}`;
  }

  async function editEvent(toast, event) {
    const form = document.createElement("form"); form.className = "cv-manager-edit-form";
    const fields = [["company", "Company"], ["role", "Role"], ["location", "Location"], ["application_date", "Applied on"]];
    for (const [key, label] of fields) {
      const input = document.createElement("input"); input.name = key; input.value = event.payload[key] || ""; input.placeholder = label;
      if (key === "application_date") input.type = "date";
      form.appendChild(input);
    }
    const select = document.createElement("select"); select.name = "cv_id";
    const unknown = document.createElement("option"); unknown.value = ""; unknown.textContent = "CV unknown"; select.appendChild(unknown);
    const cvs = await send({ type: "listCvs" });
    for (const cv of cvs.cvs || []) {
      const option = document.createElement("option"); option.value = String(cv.id); option.textContent = cv.name; option.selected = cv.id === event.payload.cv_id; select.appendChild(option);
    }
    const save = document.createElement("button"); save.type = "submit"; save.textContent = "Save";
    form.append(select, save); toast.replaceChildren(form);
    form.addEventListener("submit", async (submitEvent) => {
      submitEvent.preventDefault();
      const values = new FormData(form);
      for (const [key] of fields) event.payload[key] = String(values.get(key) || "").trim();
      event.payload.cv_id = values.get("cv_id") ? Number(values.get("cv_id")) : null;
      event.payload.cv_name = select.selectedOptions[0]?.textContent === "CV unknown" ? "" : select.selectedOptions[0]?.textContent;
      event.revision += 1;
      const response = await send({ type: "writeEvent", event });
      if (response?.ok) showLoggedToast(event); else showMessageToast(response?.error || "Could not save changes.", true);
    });
  }

  function showLoggedToast(event) {
    const toast = toastShell();
    const title = document.createElement("strong"); title.textContent = "Job logged";
    const summary = document.createElement("span"); summary.textContent = eventSummary(event);
    const actions = document.createElement("div"); actions.className = "cv-manager-toast-actions";
    const edit = document.createElement("button"); edit.type = "button"; edit.textContent = "Edit";
    const undo = document.createElement("button"); undo.type = "button"; undo.textContent = "Don’t log";
    edit.addEventListener("click", () => editEvent(toast, event));
    undo.addEventListener("click", async () => {
      event.revision += 1; event.state = "cancelled";
      const response = await send({ type: "writeEvent", event });
      if (response?.ok) showMessageToast("Application log removed."); else showMessageToast(response?.error || "Could not remove the log.", true);
    });
    actions.append(edit, undo); toast.append(title, summary, actions);
    setTimeout(() => { if (activeToast === toast) toast.remove(); }, 12_000);
  }

  document.addEventListener("click", (event) => {
    const form = event.target?.closest?.("form");
    if (looksFinal(submissionText(event.target), form)) armSubmission();
  }, true);
  document.addEventListener("submit", (event) => {
    if (looksFinal(submissionText(event.submitter), event.target)) armSubmission();
  }, true);

  const observer = new MutationObserver(() => { decorateInputs(); scheduleConfirmationCheck(); });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  decorateInputs(); scheduleConfirmationCheck(300);

  browserApi.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    let work;
    if (message.type === "attachCvData") {
      const input = [...document.querySelectorAll('input[type="file"]')].find((item) => !item.disabled);
      work = input ? attachCv(input, message.cv).then(() => ({ ok: true })) : Promise.resolve({ ok: false, error: "No résumé upload field was found." });
    } else if (message.type === "manualLog") {
      work = send({ type: "logDetected", candidate: extractCandidate() }).then((response) => {
        if (response?.ok) showLoggedToast(response.event); else showMessageToast(response?.error || "Could not log this page.", true);
        return response;
      });
    } else {
      return undefined;
    }
    if (globalThis.browser) return work;
    work.then(sendResponse);
    return true;
  });
})();

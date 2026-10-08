(() => {
  const CHECK_INTERVAL_MS = 10000;
  const REQUEST_TIMEOUT_MS = 2000;
  const RESTORED_MESSAGE_MS = 3000;
  const BANNER_ID = "officeConnectionBanner";
  let disconnected = false;
  let checkedOnce = false;
  let checkInProgress = false;
  let dailyReportFormDirty = false;
  let bannerTimer = null;

  const banner = document.createElement("aside");
  banner.id = BANNER_ID;
  banner.className = "office-connection-banner";
  banner.hidden = true;
  banner.setAttribute("role", "status");
  banner.setAttribute("aria-live", "polite");
  banner.innerHTML = `
    <svg class="office-connection-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M2 8.8a15.8 15.8 0 0 1 17.3-3.2M5.2 12a10.8 10.8 0 0 1 8.2-2.7M8.7 15.4a5.6 5.6 0 0 1 3.2-.8M3 3l18 18M12 20h.01" />
    </svg>
    <div class="office-connection-copy">
      <strong class="office-connection-title"></strong>
      <span class="office-connection-message"></span>
      <span class="office-connection-unsaved" hidden></span>
    </div>
  `;
  document.body.prepend(banner);

  function updateBanner(state) {
    const title = banner.querySelector(".office-connection-title");
    const message = banner.querySelector(".office-connection-message");
    const unsaved = banner.querySelector(".office-connection-unsaved");

    clearTimeout(bannerTimer);
    bannerTimer = null;

    if (state === "offline") {
      banner.dataset.state = "offline";
      title.textContent = "OFFICE WI-FI DISCONNECTED";
      message.textContent = "You appear to be outside the range of the Mannat Moon office Wi-Fi or disconnected from the office network. Please reconnect to continue.";
      unsaved.textContent = "Your unsaved Daily Report changes may not be saved. Keep this page open; your entries have not been cleared or submitted.";
      unsaved.hidden = !dailyReportFormDirty;
      banner.hidden = false;
      return;
    }

    if (state === "restored") {
      banner.dataset.state = "restored";
      title.textContent = "Office connection restored.";
      message.textContent = "";
      unsaved.hidden = true;
      banner.hidden = false;
      bannerTimer = setTimeout(() => {
        if (banner.dataset.state === "restored") {
          banner.hidden = true;
        }
      }, RESTORED_MESSAGE_MS);
      return;
    }

    banner.hidden = true;
  }

  function updateUnsavedWarning() {
    const unsaved = banner.querySelector(".office-connection-unsaved");
    if (banner.dataset.state === "offline") {
      unsaved.hidden = !dailyReportFormDirty;
    }
  }

  function monitorDailyReportForm() {
    const form = document.getElementById("dr-recordForm");
    if (!form) return;

    form.addEventListener("input", () => {
      dailyReportFormDirty = true;
      updateUnsavedWarning();
    });
    form.addEventListener("change", () => {
      dailyReportFormDirty = true;
      updateUnsavedWarning();
    });
    form.addEventListener("reset", () => {
      queueMicrotask(() => {
        dailyReportFormDirty = false;
        updateUnsavedWarning();
      });
    });
  }

  async function checkOfficeServer() {
    if (checkInProgress) return;
    checkInProgress = true;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

    try {
      const response = await fetch("/health", {
        method: "GET",
        cache: "no-store",
        credentials: "same-origin",
        signal: controller.signal
      });
      const reachable = response.ok;

      if (!reachable) {
        disconnected = true;
        updateBanner("offline");
      } else if (disconnected) {
        disconnected = false;
        updateBanner("restored");
      } else if (!checkedOnce) {
        updateBanner("online");
      }
      checkedOnce = true;
    } catch (_) {
      disconnected = true;
      checkedOnce = true;
      updateBanner("offline");
    } finally {
      clearTimeout(timeoutId);
      checkInProgress = false;
    }
  }

  monitorDailyReportForm();
  checkOfficeServer();
  setInterval(checkOfficeServer, CHECK_INTERVAL_MS);
})();

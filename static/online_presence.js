const HEARTBEAT_INTERVAL_MS = 60_000;
const REQUEST_TIMEOUT_MS = 10_000;
const MIN_RETRY_DELAY_SECONDS = 2;
const MAX_RETRY_DELAY_SECONDS = 60;
const INITIAL_RETRY_DELAY_SECONDS = 5;
const MAX_BACKOFF_DELAY_SECONDS = 30;

const widget = document.querySelector("[data-presence-widget]");
if (widget) {
  const status = widget.querySelector("[data-presence-status]");
  const list = widget.querySelector("[data-presence-list]");
  const summary = widget.querySelector("summary");
  const csrfToken = widget.dataset.csrfToken || "";

  function positionMobilePanel() {
    if (!summary || !widget.style) return;
    if (!widget.open || !window.matchMedia("(max-width: 620px)").matches) {
      widget.style.removeProperty("--presence-panel-top");
      return;
    }
    const top = Math.max(12, Math.ceil(summary.getBoundingClientRect().bottom + 8));
    widget.style.setProperty("--presence-panel-top", `${top}px`);
  }

  if (summary && widget.addEventListener) {
    widget.addEventListener("toggle", positionMobilePanel);
    window.addEventListener("resize", positionMobilePanel);
    window.addEventListener("scroll", positionMobilePanel, true);
  }
  let stopped = false;
  let inFlight = false;
  let retryTimer = null;
  let retryBackoffSeconds = INITIAL_RETRY_DELAY_SECONDS;
  let hasSuccessfulRoster = false;

  function showMessage(message) {
    status.textContent = message;
    list.replaceChildren();
    const item = document.createElement("li");
    item.textContent = message;
    list.append(item);
  }

  function showRecoverableMessage() {
    if (!hasSuccessfulRoster) status.textContent = "Presence unavailable; retrying";
  }

  function clearRetry() {
    if (retryTimer !== null) {
      window.clearTimeout(retryTimer);
      retryTimer = null;
    }
  }

  function scheduleRetry(delaySeconds) {
    if (stopped || retryTimer !== null) return;
    const boundedDelay = Math.min(
      MAX_RETRY_DELAY_SECONDS,
      Math.max(MIN_RETRY_DELAY_SECONDS, delaySeconds)
    );
    retryTimer = window.setTimeout(() => {
      retryTimer = null;
      void refreshPresence();
    }, boundedDelay * 1000);
  }

  function nextBackoffDelay() {
    const delay = retryBackoffSeconds;
    retryBackoffSeconds = Math.min(
      MAX_BACKOFF_DELAY_SECONDS,
      retryBackoffSeconds * 2
    );
    return delay;
  }

  function retryAfterSeconds(response) {
    const value = response.headers.get("Retry-After");
    const seconds = Number(value);
    if (Number.isFinite(seconds) && seconds > 0) return seconds;

    const retryAt = Date.parse(value);
    if (Number.isFinite(retryAt)) {
      return Math.max(0, (retryAt - Date.now()) / 1000);
    }
    return MIN_RETRY_DELAY_SECONDS;
  }

  async function requestJson(path, options) {
    const controller = new AbortController();
    let timeoutId;
    const request = (async () => {
      const response = await fetch(path, {
        ...options,
        signal: controller.signal,
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          ...(options.headers || {}),
          Accept: "application/json"
        }
      });
      const body = await response.json();
      if (!response.ok) {
        const error = new Error(
          typeof body.error === "string" ? body.error : `Presence request failed (HTTP ${response.status}).`
        );
        error.status = response.status;
        error.retryAfter = retryAfterSeconds(response);
        throw error;
      }
      return body;
    })();
    const timeout = new Promise((_, reject) => {
      timeoutId = window.setTimeout(() => {
        controller.abort();
        const error = new Error("The presence request timed out.");
        error.name = "TimeoutError";
        reject(error);
      }, REQUEST_TIMEOUT_MS);
    });

    try {
      return await Promise.race([request, timeout]);
    } finally {
      window.clearTimeout(timeoutId);
    }
  }

  async function refreshPresence() {
    if (stopped || inFlight) return;
    inFlight = true;
    let retryDelay = null;

    try {
      try {
        await requestJson("/api/presence/heartbeat", {
          method: "POST",
          headers: { "X-CSRF-Token": csrfToken }
        });
      } catch (error) {
        if (error.status === 401 || error.status === 403) {
          showMessage(error.message || "Please sign in again to view online staff.");
          stopped = true;
          clearRetry();
          return;
        }
        retryDelay = error.status === 429
          ? error.retryAfter
          : nextBackoffDelay();
      }

      try {
        const { staff } = await requestJson("/api/presence/roster", {});
        if (!Array.isArray(staff)) {
          throw new Error("The server returned an invalid online staff list.");
        }
        list.replaceChildren();
        if (staff.length === 0) {
          status.textContent = "No one online";
          const item = document.createElement("li");
          item.textContent = "No staff are currently online.";
          list.append(item);
        } else {
          status.textContent = `${staff.length} online`;
          for (const person of staff) {
            if (!person || typeof person.display_name !== "string") continue;
            const item = document.createElement("li");
            item.textContent = person.display_name;
            list.append(item);
          }
        }
        hasSuccessfulRoster = true;
        if (retryDelay === null) {
          clearRetry();
          retryBackoffSeconds = INITIAL_RETRY_DELAY_SECONDS;
        }
      } catch (error) {
        if (error.status === 401 || error.status === 403) {
          showMessage(error.message || "Please sign in again to view online staff.");
          stopped = true;
          clearRetry();
          return;
        }
        showRecoverableMessage();
        const rosterRetryDelay = error.status === 429
          ? error.retryAfter
          : nextBackoffDelay();
        retryDelay = retryDelay === null
          ? rosterRetryDelay
          : Math.max(retryDelay, rosterRetryDelay);
      }

      if (retryDelay !== null) scheduleRetry(retryDelay);
    } finally {
      inFlight = false;
    }
  }

  void refreshPresence();
  window.setInterval(refreshPresence, HEARTBEAT_INTERVAL_MS);
}

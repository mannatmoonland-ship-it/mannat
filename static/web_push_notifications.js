import { getFirebaseApp } from "./firebase_auth.js";

const settings = document.querySelector("[data-web-push-settings]");
const messagingSdkUrl = "https://www.gstatic.com/firebasejs/12.2.1/firebase-messaging.js";
let firebaseMessagingSdk;
let messagingInstance;
let currentDeviceToken = "";
let serviceWorkerRegistration;
let foregroundListenerAttached = false;

function showStatus(message, state = "info") {
  const status = document.getElementById("web-push-status");
  if (!status) return;
  status.textContent = message;
  status.dataset.state = state;
}

function describeError(error, fallback) {
  const name = typeof error?.name === "string" && error.name ? `${error.name}: ` : "";
  return `${name}${error?.message || fallback}`;
}

async function loadMessagingSdk() {
  if (!firebaseMessagingSdk) {
    firebaseMessagingSdk = import(messagingSdkUrl).then(async sdk => {
      if (!(await sdk.isSupported())) {
        throw new Error("This browser does not support Firebase Web Push.");
      }
      return sdk;
    }).catch(error => {
      firebaseMessagingSdk = undefined;
      throw error;
    });
  }
  return firebaseMessagingSdk;
}

async function requestJson(url, { method = "GET", token, csrfToken } = {}) {
  const options = {
    method,
    credentials: "same-origin",
    headers: {},
  };
  if (token !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify({ token });
  }
  if (csrfToken) options.headers["X-CSRF-Token"] = csrfToken;
  const response = await fetch(url, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "The notification request failed.");
  return result;
}

async function getPushConfiguration() {
  return requestJson("/api/notifications/config");
}

async function getMessaging() {
  if (!messagingInstance) {
    const sdk = await loadMessagingSdk();
    messagingInstance = sdk.getMessaging(await getFirebaseApp());
  }
  return { sdk: await loadMessagingSdk(), messaging: messagingInstance };
}

async function getCurrentDeviceToken(vapidKey) {
  if (!window.isSecureContext) {
    throw new Error("Browser notifications require HTTPS or localhost.");
  }
  if (!("serviceWorker" in navigator) || !("Notification" in window)) {
    throw new Error("This browser does not support service workers or notifications.");
  }
  serviceWorkerRegistration = await navigator.serviceWorker.register(
    "/firebase-messaging-sw.js",
    { scope: "/" },
  );
  const { sdk, messaging } = await getMessaging();
  currentDeviceToken = await sdk.getToken(messaging, {
    vapidKey,
    serviceWorkerRegistration,
  });
  if (!currentDeviceToken) throw new Error("The browser did not provide a notification token.");
  return currentDeviceToken;
}

function notificationUrl(value) {
  try {
    const target = new URL(value || "/red-book", window.location.origin);
    return target.origin === window.location.origin ? target.href : "/red-book";
  } catch {
    return "/red-book";
  }
}

async function showForegroundNotification(payload) {
  const data = payload.data || {};
  if (Notification.permission !== "granted") return;
  const title = data.title || "Mannat Moon Construction";
  const options = {
    body: data.body || "There is a new Red Book update.",
    tag: data.event_id || undefined,
    renotify: false,
    data: { url: notificationUrl(data.url) },
  };
  if (serviceWorkerRegistration) {
    await serviceWorkerRegistration.showNotification(title, options);
    return;
  }
  const notification = new Notification(title, options);
  notification.onclick = () => {
    window.focus();
    window.location.assign(options.data.url);
  };
}

async function attachForegroundListener() {
  if (foregroundListenerAttached || Notification.permission !== "granted") return;
  const { sdk, messaging } = await getMessaging();
  serviceWorkerRegistration = serviceWorkerRegistration
    || await navigator.serviceWorker.getRegistration("/");
  sdk.onMessage(messaging, payload => {
    showForegroundNotification(payload).catch(() => {
      if (settings) showStatus("A notification arrived, but this browser could not display it.", "error");
    });
  });
  foregroundListenerAttached = true;
}

async function initializeSettings() {
  if (!settings) return;
  const enableButton = document.getElementById("web-push-enable");
  const disableButton = document.getElementById("web-push-disable");
  const unsupportedHint = "On iPhone or iPad, use a supported Safari version and add this site to your Home Screen if required.";
  try {
    if (!window.isSecureContext) {
      enableButton.disabled = true;
      showStatus(
        "Notifications require HTTPS, or localhost on this computer. This page is using an insecure address; open localhost here or use an HTTPS address on other devices.",
        "error",
      );
      return;
    }
    if (!("serviceWorker" in navigator) || !("Notification" in window)) {
      enableButton.disabled = true;
      showStatus(`This browser does not support the required notification APIs. ${unsupportedHint}`, "error");
      return;
    }
    const sdk = await loadMessagingSdk();
    const configuration = await getPushConfiguration();
    if (!sdk || !configuration.configured) {
      enableButton.disabled = true;
      showStatus(configuration.message || "Web Push is not configured for this site yet.", "error");
      return;
    }
    if (Notification.permission === "denied") {
      enableButton.disabled = true;
      showStatus("Notifications are blocked. Allow them for this site in your browser or device settings, then reload this page.", "error");
      return;
    }
    if (Notification.permission === "default") {
      showStatus("Notifications are disabled until you choose Enable Notifications.");
      return;
    }

    await attachForegroundListener();
    const token = await getCurrentDeviceToken(configuration.vapid_key);
    const result = await requestJson("/api/notifications/device/status", {
      method: "POST",
      token,
      csrfToken: settings.dataset.csrfToken,
    });
    if (result.registered) {
      enableButton.hidden = true;
      disableButton.hidden = false;
      showStatus("Notifications are enabled on this device.", "success");
    } else {
      showStatus("Browser permission is granted, but notifications are not enabled on this device.");
    }
  } catch (error) {
    enableButton.disabled = true;
    const message = describeError(error, "Unable to check browser notification settings.");
    showStatus(
      `${message}${message.includes("does not support") ? ` ${unsupportedHint}` : ""}`,
      "error",
    );
  }
}

async function enableNotifications() {
  const enableButton = document.getElementById("web-push-enable");
  const disableButton = document.getElementById("web-push-disable");
  enableButton.disabled = true;
  try {
    await loadMessagingSdk();
    const configuration = await getPushConfiguration();
    if (!configuration.configured) throw new Error(configuration.message || "Web Push is not configured for this site yet.");
    const permission = Notification.permission === "default"
      ? await Notification.requestPermission()
      : Notification.permission;
    if (permission === "denied") {
      showStatus("Notifications are blocked. Allow them for this site in your browser or device settings.", "error");
      return;
    }
    if (permission !== "granted") {
      showStatus("Notification permission was not granted.");
      return;
    }
    const token = await getCurrentDeviceToken(configuration.vapid_key);
    await requestJson("/api/notifications/device", {
      method: "POST",
      token,
      csrfToken: settings.dataset.csrfToken,
    });
    await attachForegroundListener();
    enableButton.hidden = true;
    disableButton.hidden = false;
    showStatus("Notifications are enabled on this device.", "success");
  } catch (error) {
    showStatus(describeError(error, "Unable to enable notifications on this device."), "error");
  } finally {
    enableButton.disabled = false;
  }
}

async function disableNotifications() {
  const enableButton = document.getElementById("web-push-enable");
  const disableButton = document.getElementById("web-push-disable");
  disableButton.disabled = true;
  try {
    let token = currentDeviceToken;
    if (!token) {
      const configuration = await getPushConfiguration();
      if (!configuration.configured) throw new Error(configuration.message || "Web Push is not configured for this site yet.");
      token = await getCurrentDeviceToken(configuration.vapid_key);
    }
    await requestJson("/api/notifications/device", {
      method: "DELETE",
      token,
      csrfToken: settings.dataset.csrfToken,
    });
    const { sdk, messaging } = await getMessaging();
    await sdk.deleteToken(messaging);
    currentDeviceToken = "";
    disableButton.hidden = true;
    enableButton.hidden = false;
    showStatus("Notifications were disabled on this device. Other devices are unchanged.", "success");
  } catch (error) {
    showStatus(error.message || "Unable to disable notifications on this device.", "error");
  } finally {
    disableButton.disabled = false;
  }
}

if (settings) {
  document.getElementById("web-push-enable").addEventListener("click", enableNotifications);
  document.getElementById("web-push-disable").addEventListener("click", disableNotifications);
  initializeSettings();
} else if ("Notification" in window && Notification.permission === "granted") {
  attachForegroundListener().catch(() => {});
}

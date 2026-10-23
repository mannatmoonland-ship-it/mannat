import { createRedBookSiren } from "./red_book_notification_sound.js";
import { createRedBookForegroundNotificationHandler } from "./red_book_foreground_notification.js";

const settings = document.querySelector("[data-web-push-settings]");
const messagingSdkUrl = "https://www.gstatic.com/firebasejs/12.2.1/firebase-messaging.js";
const firebaseAppSdkUrl = "https://www.gstatic.com/firebasejs/12.2.1/firebase-app.js";
let firebaseMessagingSdk;
let firebaseAppSdk;
let firebaseWebConfigPromise;
let messagingInstance;
let currentDeviceToken = "";
let serviceWorkerRegistration;
let foregroundListenerPromise;
const playRedBookSiren = createRedBookSiren();
const handleForegroundNotification = createRedBookForegroundNotificationHandler({
  getRegistration: () => serviceWorkerRegistration,
  playSiren: playRedBookSiren,
  onSirenFailure: error => {
    if (!settings) return;
    const message = error?.name === "NotAllowedError"
      ? "The Red Book notification was displayed, but the browser blocked siren audio. Click Test Siren and check this tab's sound settings."
      : "The Red Book notification was displayed, but the siren could not play. Click Test Siren to check the audio.";
    showStatus(message, "error");
  },
});

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

function describeNotificationError(error, fallback) {
  if (error?.name === "AbortError" && /push service error/i.test(error?.message || "")) {
    return "Brave's push service rejected the subscription. Open brave://settings/privacy and enable “Use Google services for push messaging,” then check that notifications are allowed for this site and temporarily turn off Shields or VPN/DNS filtering. Also confirm FIREBASE_WEB_PUSH_VAPID_KEY is the Web Push key for this same Firebase project. Reload and try again; if it still fails, test in Chrome or Edge.";
  }
  return describeError(error, fallback);
}

function logNotificationError(message, error) {
  console.error(message, {
    name: error?.name || "Error",
    code: error?.code || "",
    stage: error?.stage || "",
  });
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

async function loadFirebaseAppSdk() {
  if (!firebaseAppSdk) {
    firebaseAppSdk = import(firebaseAppSdkUrl).catch(error => {
      firebaseAppSdk = undefined;
      throw error;
    });
  }
  return firebaseAppSdk;
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

async function getFirebaseWebConfig() {
  if (!firebaseWebConfigPromise) {
    firebaseWebConfigPromise = requestJson("/api/firebase-web-config")
      .catch(error => {
        firebaseWebConfigPromise = undefined;
        throw error;
      });
  }
  return firebaseWebConfigPromise;
}

async function getPushConfiguration() {
  return requestJson("/api/notifications/config");
}

async function getMessaging() {
  if (!messagingInstance) {
    const [sdk, appSdk, config] = await Promise.all([
      loadMessagingSdk(),
      loadFirebaseAppSdk(),
      getFirebaseWebConfig(),
    ]);
    const existingApp = appSdk.getApps().find(firebaseApp => firebaseApp.name === "[DEFAULT]");
    const firebaseApp = existingApp || appSdk.initializeApp(config);
    if (firebaseApp.options.projectId !== config.projectId) {
      throw new Error("The initialized Firebase project does not match the active project.");
    }
    messagingInstance = sdk.getMessaging(firebaseApp);
    console.info("Firebase Messaging initialized.");
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
  serviceWorkerRegistration = await navigator.serviceWorker.ready;
  console.info("Firebase Messaging service worker is ready.", {
    state: serviceWorkerRegistration.active?.state || "no-active-worker",
  });
  const { sdk, messaging } = await getMessaging();
  currentDeviceToken = await sdk.getToken(messaging, {
    vapidKey,
    serviceWorkerRegistration,
  });
  if (!currentDeviceToken) throw new Error("The browser did not provide a notification token.");
  return currentDeviceToken;
}

async function attachForegroundListener() {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  if (!foregroundListenerPromise) {
    foregroundListenerPromise = (async () => {
      const { sdk, messaging } = await getMessaging();
      serviceWorkerRegistration = serviceWorkerRegistration
        || await navigator.serviceWorker.getRegistration("/");
      sdk.onMessage(messaging, payload => {
        console.info("Received a Firebase foreground message.");
        handleForegroundNotification(payload).catch(error => {
          logNotificationError("Red Book foreground notification could not be displayed.", error);
          if (settings) showStatus("A Red Book notification arrived, but this browser could not display it.", "error");
        });
      });
      console.info("Firebase foreground message listener registered.");
    })().catch(error => {
      foregroundListenerPromise = undefined;
      throw error;
    });
  }
  await foregroundListenerPromise;
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
    const configuration = await getPushConfiguration();
    if (!configuration.configured) {
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
    logNotificationError("Red Book notification initialization failed.", error);
    enableButton.disabled = true;
    const message = describeNotificationError(error, "Unable to check browser notification settings.");
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
    await attachForegroundListener();
    await requestJson("/api/notifications/device", {
      method: "POST",
      token,
      csrfToken: settings.dataset.csrfToken,
    });
    enableButton.hidden = true;
    disableButton.hidden = false;
    showStatus("Notifications are enabled on this device.", "success");
  } catch (error) {
    logNotificationError("Red Book notifications could not be enabled.", error);
    showStatus(describeNotificationError(error, "Unable to enable notifications on this device."), "error");
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

async function testRedBookSiren() {
  const testButton = document.getElementById("web-push-test-siren");
  testButton.disabled = true;
  try {
    await playRedBookSiren.test();
    showStatus("Siren test played. Keep this page open and in the foreground to hear Red Book alerts.", "success");
  } catch (error) {
    logNotificationError("Red Book siren test failed.", error);
    const message = error?.name === "NotAllowedError"
      ? "The browser blocked siren audio. Allow sound for this site, click Test Siren again, and keep this page open in the foreground."
      : "The siren audio could not load or play. Check the siren file request and this device's volume, then click Test Siren again.";
    showStatus(message, "error");
  } finally {
    testButton.disabled = false;
  }
}

if (settings) {
  document.getElementById("web-push-enable").addEventListener("click", enableNotifications);
  document.getElementById("web-push-disable").addEventListener("click", disableNotifications);
  document.getElementById("web-push-test-siren").addEventListener("click", testRedBookSiren);
  initializeSettings();
} else if ("Notification" in window && Notification.permission === "granted") {
  attachForegroundListener().catch(error => {
    logNotificationError("Firebase foreground message listener could not be registered.", error);
  });
}

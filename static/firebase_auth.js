const REQUEST_TIMEOUT_MS = 15000;
const REQUIRED_CONFIG_FIELDS = ["apiKey", "authDomain", "projectId", "appId"];
let firebaseConfigPromise;
let firebaseAppPromise;
let authPromise;
let firebaseSdkPromise;

export class FirebaseBootstrapError extends Error {
  constructor(stage, message, { code = "", status = 0, cause } = {}) {
    super(message, { cause });
    this.name = "FirebaseBootstrapError";
    this.stage = stage;
    this.code = code;
    this.status = status;
  }
}

function loadFirebaseSdk() {
  if (!firebaseSdkPromise) {
    firebaseSdkPromise = Promise.all([
      import("https://www.gstatic.com/firebasejs/12.2.1/firebase-app.js"),
      import("https://www.gstatic.com/firebasejs/12.2.1/firebase-auth.js"),
      import("https://www.gstatic.com/firebasejs/12.2.1/firebase-firestore.js")
    ]).then(([appSdk, authSdk, firestoreSdk]) => ({ appSdk, authSdk, firestoreSdk })).catch(error => {
      firebaseSdkPromise = undefined;
      throw new FirebaseBootstrapError(
        "sdk-load",
        "The Firebase browser SDK could not be loaded. Check the network and reload the page.",
        { code: "network-error", cause: error }
      );
    });
  }
  return firebaseSdkPromise;
}

export async function getFirebaseFirestoreSdk() {
  return (await loadFirebaseSdk()).firestoreSdk;
}

function withTimeout(promise, stage) {
  let timeoutId;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timeoutId = setTimeout(() => reject(new FirebaseBootstrapError(
        stage,
        `Firebase ${stage} timed out after ${REQUEST_TIMEOUT_MS / 1000} seconds.`,
        { code: "timeout" }
      )), REQUEST_TIMEOUT_MS);
    })
  ]).finally(() => clearTimeout(timeoutId));
}

async function requestJson(path, options, stage) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(path, {
      ...options,
      credentials: "same-origin",
      cache: "no-store",
      signal: controller.signal
    });
    const contentType = response.headers.get("content-type") || "";
    if (!response.ok) {
      let message = `Firebase ${stage} failed (HTTP ${response.status}).`;
      if (contentType.includes("application/json")) {
        const body = await response.json();
        if (typeof body.error === "string") message = body.error;
      }
      throw new FirebaseBootstrapError(stage, message, { status: response.status });
    }
    if (!contentType.toLowerCase().includes("application/json")) {
      throw new FirebaseBootstrapError(stage, `Firebase ${stage} returned an unexpected response type.`);
    }
    return await response.json();
  } catch (error) {
    if (error instanceof FirebaseBootstrapError) throw error;
    const message = error?.name === "AbortError"
      ? `Firebase ${stage} timed out after ${REQUEST_TIMEOUT_MS / 1000} seconds.`
      : `Firebase ${stage} request failed. Check the application connection and sign-in session.`;
    throw new FirebaseBootstrapError(stage, message, {
      code: error?.name === "AbortError" ? "timeout" : "network-error",
      cause: error
    });
  } finally {
    clearTimeout(timeoutId);
  }
}

function validateFirebaseConfig(config) {
  if (!config || typeof config !== "object" || Array.isArray(config)) {
    throw new FirebaseBootstrapError("web-config", "The server returned an invalid Firebase Web configuration.");
  }
  for (const field of REQUIRED_CONFIG_FIELDS) {
    if (typeof config[field] !== "string" || !config[field].trim()) {
      throw new FirebaseBootstrapError("web-config", `The Firebase Web configuration is missing ${field}.`);
    }
  }
}

function getFirebaseConfig() {
  if (!firebaseConfigPromise) {
    firebaseConfigPromise = requestJson("/api/firebase-web-config", {}, "web-config")
      .then(config => {
        validateFirebaseConfig(config);
        return config;
      })
      .catch(error => {
        firebaseConfigPromise = undefined;
        throw error;
      });
  }
  return firebaseConfigPromise;
}

export function getFirebaseApp() {
  if (!firebaseAppPromise) {
    firebaseAppPromise = Promise.all([
      getFirebaseConfig(),
      withTimeout(loadFirebaseSdk(), "sdk-load")
    ]).then(([config, { appSdk }]) => {
      const existingApp = appSdk.getApps().find(firebaseApp => firebaseApp.name === "[DEFAULT]");
      const firebaseApp = existingApp || appSdk.initializeApp(config);
      if (firebaseApp.options.projectId !== config.projectId) {
        throw new FirebaseBootstrapError("web-config", "The initialized Firebase project does not match the active project.");
      }
      return firebaseApp;
    }).catch(error => {
      firebaseAppPromise = undefined;
      throw error;
    });
  }
  return firebaseAppPromise;
}

async function initializeFirebaseAuth() {
  try {
    const firebaseApp = await getFirebaseApp();
    const { authSdk } = await loadFirebaseSdk();
    const auth = authSdk.getAuth(firebaseApp);
    await withTimeout(authSdk.setPersistence(auth, authSdk.browserLocalPersistence), "auth-persistence");
    return auth;
  } catch (error) {
    if (error instanceof FirebaseBootstrapError) throw error;
    throw new FirebaseBootstrapError("auth-initialization", "Firebase Authentication could not be initialized.", {
      code: error?.code || "",
      cause: error
    });
  }
}

export function getFirebaseAuth() {
  if (!authPromise) {
    authPromise = initializeFirebaseAuth().catch(error => {
      authPromise = undefined;
      throw error;
    });
  }
  return authPromise;
}

async function requestCustomToken() {
  const { csrf_token: csrfToken } = await requestJson(
    "/api/firebase-custom-token",
    {},
    "custom-token-csrf"
  );
  if (typeof csrfToken !== "string" || !csrfToken) {
    throw new FirebaseBootstrapError("custom-token-csrf", "The server did not provide an authentication request token.");
  }

  const { token } = await requestJson("/api/firebase-custom-token", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken
    },
    body: "{}"
  }, "custom-token-issuance");
  if (typeof token !== "string" || !token) {
    throw new FirebaseBootstrapError("custom-token-issuance", "The server did not provide a Firebase authentication token.");
  }
  return token;
}

export async function reauthenticateFirebase() {
  try {
    const auth = await getFirebaseAuth();
    const token = await requestCustomToken();
    const { authSdk } = await loadFirebaseSdk();
    const credential = await withTimeout(
      authSdk.signInWithCustomToken(auth, token),
      "custom-token-sign-in"
    );
    return credential.user;
  } catch (error) {
    if (error instanceof FirebaseBootstrapError) throw error;
    throw new FirebaseBootstrapError(
      "custom-token-sign-in",
      error?.code === "auth/configuration-not-found"
        ? "Firebase Authentication is not configured for the selected project. Contact the Firebase administrator."
        : `Firebase custom-token sign-in failed${error?.code ? ` (${error.code})` : ""}.`,
      { code: error?.code || "", cause: error }
    );
  }
}

export const firebaseAuthReady = reauthenticateFirebase();

export function describeFirebaseError(error) {
  if (error?.code === "auth/configuration-not-found") {
    return "Firebase Authentication is not configured for the selected project. Contact the Firebase administrator.";
  }
  if (error?.code === "permission-denied") {
    return "Firebase denied this read. Check the account permissions and deployed Firestore Rules.";
  }
  if (error?.code === "unauthenticated") {
    return "Firebase sign-in is no longer valid. Sign in again and reload this page.";
  }
  if (error?.code === "unavailable" || error?.code === "network-error" || error?.code === "timeout") {
    return "Firebase could not be reached. Check the connection and retry.";
  }
  return error?.message || "Firebase could not be initialized. Reload the page or contact the administrator.";
}

export async function signOutFirebase() {
  const auth = await getFirebaseAuth();
  const { authSdk } = await loadFirebaseSdk();
  await authSdk.signOut(auth);
}

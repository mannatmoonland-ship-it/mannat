import assert from "node:assert/strict";
import test from "node:test";
import { initializeConnectivityIndicator } from "../static/connectivity_indicator.js";

function createBrowserFixture({ online, hasHeader = true, hasLoginShell = false }) {
  let banner;
  const listeners = new Map();
  const header = hasHeader
    ? {
        before(element) {
          banner = element;
        },
      }
    : null;
  const loginShell = hasLoginShell
    ? {
        prepend(element) {
          banner = element;
        },
      }
    : null;
  const documentApi = {
    createElement() {
      return {
        attributes: {},
        hidden: false,
        setAttribute(name, value) {
          this.attributes[name] = value;
        },
      };
    },
    querySelector(selector) {
      if (selector === "header.mm-app-header") return header;
      if (selector === ".login-shell") return loginShell;
      return null;
    },
    body: {
      prepend(element) {
        banner = element;
      },
    },
  };
  const navigatorApi = { onLine: online };
  const windowApi = {
    addEventListener(type, listener) {
      listeners.set(type, listener);
    },
  };

  return {
    banner: () => banner,
    dispatch(type, nextOnlineState) {
      navigatorApi.onLine = nextOnlineState;
      listeners.get(type)();
    },
    documentApi,
    navigatorApi,
    windowApi,
  };
}

test("offline state is shown immediately and tracks later connectivity changes", () => {
  const fixture = createBrowserFixture({ online: false });
  const banner = initializeConnectivityIndicator(
    fixture.documentApi,
    fixture.navigatorApi,
    fixture.windowApi,
  );

  assert.equal(fixture.banner(), banner);
  assert.equal(banner.hidden, false);
  assert.equal(banner.textContent, "You are offline. Please check your internet connection.");
  assert.deepEqual(banner.attributes, {
    role: "status",
    "aria-live": "polite",
    "aria-atomic": "true",
  });

  fixture.dispatch("online", true);
  assert.equal(banner.hidden, true);

  fixture.dispatch("offline", false);
  assert.equal(banner.hidden, false);
});

test("online state starts hidden and login pages place the banner inside the login shell", () => {
  const fixture = createBrowserFixture({
    online: true,
    hasHeader: false,
    hasLoginShell: true,
  });
  const banner = initializeConnectivityIndicator(
    fixture.documentApi,
    fixture.navigatorApi,
    fixture.windowApi,
  );

  assert.equal(fixture.banner(), banner);
  assert.equal(banner.hidden, true);

  fixture.dispatch("offline", false);
  assert.equal(banner.hidden, false);
});

test("pages without a shared header place the banner at the start of the document", () => {
  const fixture = createBrowserFixture({ online: false, hasHeader: false });
  const banner = initializeConnectivityIndicator(
    fixture.documentApi,
    fixture.navigatorApi,
    fixture.windowApi,
  );

  assert.equal(fixture.banner(), banner);
  assert.equal(banner.hidden, false);
});

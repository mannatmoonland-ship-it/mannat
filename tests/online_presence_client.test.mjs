import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import { test } from "node:test";

const script = await readFile(
  new URL("../static/online_presence.js", import.meta.url),
  "utf8"
);

function createHarness(fetch) {
  const status = { textContent: "Loading..." };
  const list = {
    children: [],
    replaceChildren() {
      this.children = [];
    },
    append(item) {
      this.children.push(item);
    }
  };
  const styleValues = new Map();
  const widget = {
    dataset: { csrfToken: "test-csrf-token" },
    open: false,
    style: {
      setProperty(name, value) { styleValues.set(name, value); },
      removeProperty(name) { styleValues.delete(name); }
    },
    addEventListener() {},
    querySelector(selector) {
      if (selector === "[data-presence-status]") return status;
      if (selector === "[data-presence-list]") return list;
      if (selector === "summary") return { getBoundingClientRect: () => ({ bottom: 80 }) };
      return null;
    }
  };
  let nextTimerId = 1;
  const timers = new Map();
  const intervals = [];
  const context = {
    AbortController,
    Date,
    Number,
    Promise,
    document: {
      querySelector: () => widget,
      createElement: () => ({ textContent: "" })
    },
    fetch,
    window: {
      setTimeout(callback, delay) {
        const id = nextTimerId++;
        timers.set(id, { callback, delay });
        return id;
      },
      clearTimeout(id) {
        timers.delete(id);
      },
      setInterval(callback, delay) {
        intervals.push({ callback, delay });
        return intervals.length;
      },
      matchMedia: () => ({ matches: false }),
      addEventListener() {}
    }
  };

  vm.runInNewContext(script, context);
  return { status, list, timers, intervals, widget, styleValues };
}

function response(body, status = 200, retryAfter = null) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get(name) {
        return name.toLowerCase() === "retry-after" ? retryAfter : null;
      }
    },
    json: async () => body
  };
}

async function settlePromises() {
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
}

function timerDelays(harness) {
  return [...harness.timers.values()].map(({ delay }) => delay);
}

test("successful heartbeat and roster preserve the online staff display", async () => {
  const requests = [];
  const harness = createHarness(async (path, options) => {
    requests.push({ path, options });
    return path.endsWith("heartbeat")
      ? response({ success: true })
      : response({ staff: [{ display_name: "Trusted Staff Name" }] });
  });

  await settlePromises();

  assert.deepEqual(
    requests.map(({ path }) => path),
    ["/api/presence/heartbeat", "/api/presence/roster"]
  );
  assert.equal(requests[0].options.method, "POST");
  assert.equal(requests[0].options.headers["X-CSRF-Token"], "test-csrf-token");
  assert.equal(harness.status.textContent, "1 online");
  assert.deepEqual(
    harness.list.children.map(({ textContent }) => textContent),
    ["Trusted Staff Name"]
  );
  assert.deepEqual(timerDelays(harness), []);
  assert.equal(harness.intervals.length, 1);
  assert.equal(harness.intervals[0].delay, 60_000);
});


test("routine refreshes and failures preserve the last successful count and roster", async () => {
  let failRoster = false;
  let rosterCalls = 0;
  let pendingRosterResolve;
  const harness = createHarness(async (path) => {
    if (path.endsWith("heartbeat")) return response({ success: true });
    rosterCalls += 1;
    if (rosterCalls === 1) return response({ staff: [{ display_name: "Online staff" }] });
    if (failRoster) throw new Error("temporary network failure");
    return new Promise((resolve) => { pendingRosterResolve = resolve; });
  });

  await settlePromises();
  assert.equal(harness.status.textContent, "1 online");
  assert.deepEqual(harness.list.children.map(({ textContent }) => textContent), ["Online staff"]);

  harness.intervals[0].callback();
  await settlePromises();
  assert.equal(harness.status.textContent, "1 online");
  assert.deepEqual(harness.list.children.map(({ textContent }) => textContent), ["Online staff"]);
  pendingRosterResolve(response({ staff: [{ display_name: "Online staff" }, { display_name: "Another staff member" }] }));
  await settlePromises();
  assert.equal(harness.status.textContent, "2 online");

  failRoster = true;
  harness.intervals[0].callback();
  await settlePromises();
  assert.equal(harness.status.textContent, "2 online");
  assert.deepEqual(harness.list.children.map(({ textContent }) => textContent), ["Online staff", "Another staff member"]);
});

test("overlapping interval triggers do not start another refresh sequence", async () => {
  const requests = [];
  const activeRequests = new Set();
  let maxActiveRequests = 0;
  const harness = createHarness((path) => new Promise((resolve) => {
    const request = { path, resolve };
    requests.push(request);
    activeRequests.add(request);
    maxActiveRequests = Math.max(maxActiveRequests, activeRequests.size);
    request.finish = (result) => {
      activeRequests.delete(request);
      resolve(result);
    };
  }));

  harness.intervals[0].callback();
  assert.equal(requests.length, 1);
  requests[0].finish(response({ success: true }));
  await settlePromises();
  assert.equal(requests.length, 2);
  harness.intervals[0].callback();
  assert.equal(requests.length, 2);

  requests[1].finish(response({ staff: [] }));
  await settlePromises();

  assert.equal(maxActiveRequests, 1);
  assert.equal(harness.status.textContent, "No one online");
});

test("heartbeat 429 honors Retry-After and still requests the roster", async () => {
  const requests = [];
  const harness = createHarness(async (path) => {
    requests.push(path);
    return path.endsWith("heartbeat")
      ? response({ error: "too soon" }, 429, "25")
      : response({ staff: [{ display_name: "Online staff" }] });
  });

  await settlePromises();

  assert.deepEqual(requests, [
    "/api/presence/heartbeat",
    "/api/presence/roster"
  ]);
  assert.equal(harness.status.textContent, "1 online");
  assert.deepEqual(timerDelays(harness), [25_000]);
});

test("a pending roster times out, shows recoverable status and releases the guard", async () => {
  const requests = [];
  let rosterSignal;
  let rosterCalls = 0;
  const harness = createHarness((path, options) => {
    requests.push(path);
    if (path.endsWith("heartbeat")) return Promise.resolve(response({ success: true }));
    rosterCalls += 1;
    if (rosterCalls > 1) return Promise.resolve(response({ staff: [] }));
    rosterSignal = options.signal;
    return new Promise(() => {});
  });
  await settlePromises();

  assert.equal(harness.status.textContent, "Loading...");
  assert.deepEqual(timerDelays(harness), [10_000]);

  const [timeoutId, timeout] = [...harness.timers.entries()][0];
  harness.timers.delete(timeoutId);
  timeout.callback();
  await settlePromises();

  assert.equal(rosterSignal.aborted, true);
  assert.equal(harness.status.textContent, "Presence unavailable; retrying");
  assert.deepEqual(timerDelays(harness), [5_000]);

  harness.intervals[0].callback();
  await settlePromises();
  assert.equal(requests.length, 4);
  assert.equal(harness.status.textContent, "No one online");
  assert.deepEqual(timerDelays(harness), []);
});

test("transient failures use increasing, non-rapid retry delays", async () => {
  const harness = createHarness(async (path) => {
    if (path.endsWith("heartbeat")) throw new Error("network unavailable");
    return response({ staff: [] });
  });

  await settlePromises();
  assert.deepEqual(timerDelays(harness), [5_000]);

  const [timerId, timer] = [...harness.timers.entries()][0];
  harness.timers.delete(timerId);
  timer.callback();
  await settlePromises();

  assert.deepEqual(timerDelays(harness), [10_000]);
  assert.equal(harness.status.textContent, "No one online");
});

test("roster errors release request timers and allow a later interval refresh", async () => {
  let failRosterOnce = true;
  let heartbeatCount = 0;
  const harness = createHarness(async (path) => {
    if (path.endsWith("heartbeat")) {
      heartbeatCount += 1;
      return response({ success: true });
    }
    if (failRosterOnce) {
      failRosterOnce = false;
      throw new Error("temporary network failure");
    }
    return response({ staff: [] });
  });

  await settlePromises();
  assert.equal(harness.status.textContent, "Presence unavailable; retrying");
  assert.deepEqual(timerDelays(harness), [5_000]);

  harness.intervals[0].callback();
  await settlePromises();
  assert.equal(heartbeatCount, 2);
  assert.equal(harness.status.textContent, "No one online");
  assert.deepEqual(timerDelays(harness), []);
});

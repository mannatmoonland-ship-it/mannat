import assert from "node:assert/strict";
import test from "node:test";
import { createRedBookForegroundNotificationHandler } from "../static/red_book_foreground_notification.js";

const redBookPayload = (eventId, overrides = {}) => ({
  data: {
    title: "Mannat Moon Construction — Red Book",
    body: "A Red Book item has been pinned.",
    event_id: eventId,
    url: "/red-book#red-book-item-1",
    ...overrides,
  },
});

function createFixture({ playSiren, permission = "granted", registration } = {}) {
  const displayed = [];
  const audioAttempts = [];
  const logs = [];
  class FakeNotification {
    static permission = permission;

    constructor(title, options) {
      displayed.push({ title, options, method: "page" });
    }
  }
  const logger = Object.fromEntries(
    ["info", "warn", "error"].map(level => [
      level,
      (...args) => logs.push({ level, args }),
    ]),
  );
  const handler = createRedBookForegroundNotificationHandler({
    NotificationApi: FakeNotification,
    getRegistration: () => registration || {
      showNotification: async (title, options) => {
        displayed.push({ title, options, method: "service-worker" });
      },
    },
    playSiren: payload => {
      audioAttempts.push(payload.data.event_id);
      return playSiren ? playSiren(payload) : Promise.resolve(true);
    },
    windowApi: {
      location: { origin: "https://mannat.example", assign() {} },
      focus() {},
    },
    logger,
  });
  return { handler, displayed, audioAttempts, logs };
}

test("a siren failure does not block a valid Red Book notification", async () => {
  const fixture = createFixture({
    playSiren: () => Promise.reject(Object.assign(new Error("autoplay blocked"), { name: "NotAllowedError" })),
  });

  assert.equal(await fixture.handler(redBookPayload("event-a")), true);
  assert.equal(fixture.displayed.length, 1);
  assert.equal(fixture.displayed[0].method, "service-worker");
  assert.equal(fixture.displayed[0].title, "Mannat Moon Construction — Red Book");
  assert.equal(fixture.displayed[0].options.body, "A Red Book item has been pinned.");
  assert.deepEqual(fixture.audioAttempts, ["event-a"]);
  assert.equal(fixture.logs.some(entry => entry.level === "warn"), true);
  assert.equal(JSON.stringify(fixture.logs).includes("autoplay blocked"), false);
});

test("duplicate Red Book event IDs display and sound only once", async () => {
  const fixture = createFixture();

  assert.equal(await fixture.handler(redBookPayload("event-duplicate")), true);
  assert.equal(await fixture.handler(redBookPayload("event-duplicate")), false);
  assert.equal(fixture.displayed.length, 1);
  assert.deepEqual(fixture.audioAttempts, ["event-duplicate"]);
});

test("unrelated messages do not display a notification or play the siren", async () => {
  const fixture = createFixture();

  assert.equal(await fixture.handler({ data: { title: "Other notification", event_id: "other" } }), false);
  assert.equal(await fixture.handler(redBookPayload("")), false);
  assert.equal(fixture.displayed.length, 0);
  assert.deepEqual(fixture.audioAttempts, []);
});

test("notification permission denial prevents display and siren playback", async () => {
  const fixture = createFixture({ permission: "denied" });

  assert.equal(await fixture.handler(redBookPayload("event-denied")), false);
  assert.equal(fixture.displayed.length, 0);
  assert.deepEqual(fixture.audioAttempts, []);
  assert.equal(fixture.logs.some(entry => entry.level === "warn"), true);
});

test("service-worker display failure falls back to a page notification", async () => {
  const fixture = createFixture({
    registration: {
      showNotification: () => Promise.reject(Object.assign(new Error("worker display failed"), { name: "InvalidStateError" })),
    },
  });

  assert.equal(await fixture.handler(redBookPayload("event-fallback")), true);
  assert.equal(fixture.displayed.length, 1);
  assert.equal(fixture.displayed[0].method, "page");
  assert.deepEqual(fixture.audioAttempts, ["event-fallback"]);
});

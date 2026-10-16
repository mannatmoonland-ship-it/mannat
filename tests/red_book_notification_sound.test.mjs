import assert from "node:assert/strict";
import test from "node:test";
import { createRedBookChime } from "../static/red_book_notification_sound.js";

const pinEvent = eventId => ({
  data: {
    title: "Mannat Moon Construction — Red Book",
    event_id: eventId,
  },
});

test("plays one local chime for a Red Book pin event", () => {
  const calls = [];
  class FakeAudio {
    constructor(src) {
      calls.push({ src });
    }

    addEventListener() {}

    play() {
      calls.push({ played: true });
      return Promise.resolve();
    }
  }
  const playChime = createRedBookChime({ AudioConstructor: FakeAudio });

  assert.equal(playChime(pinEvent("event-1")), true);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].src, "/static/audio/red-book-chime.wav");
  assert.deepEqual(calls[1], { played: true });
});

test("does not play again for the same event ID", () => {
  let playCount = 0;
  class FakeAudio {
    addEventListener() {}

    play() {
      playCount += 1;
      return Promise.resolve();
    }
  }
  const playChime = createRedBookChime({ AudioConstructor: FakeAudio });

  assert.equal(playChime(pinEvent("event-duplicate")), true);
  assert.equal(playChime(pinEvent("event-duplicate")), false);
  assert.equal(playCount, 1);
});

test("plays once for each distinct Red Book event", () => {
  let playCount = 0;
  class FakeAudio {
    addEventListener() {}

    play() {
      playCount += 1;
      return Promise.resolve();
    }
  }
  const playChime = createRedBookChime({ AudioConstructor: FakeAudio });

  assert.equal(playChime(pinEvent("event-a")), true);
  assert.equal(playChime(pinEvent("event-b")), true);
  assert.equal(playCount, 2);
});

test("does not chime for unrelated notifications or missing event IDs", () => {
  let playCount = 0;
  class FakeAudio {
    play() {
      playCount += 1;
      return Promise.resolve();
    }
  }
  const playChime = createRedBookChime({ AudioConstructor: FakeAudio });

  assert.equal(playChime({ data: { title: "Other notification", event_id: "event-2" } }), false);
  assert.equal(playChime({ data: { title: "Mannat Moon Construction — Red Book" } }), false);
  assert.equal(playCount, 0);
});

test("playback rejection and synchronous audio failures do not escape", async () => {
  class RejectedAudio {
    addEventListener() {}

    play() {
      return Promise.reject(new Error("autoplay blocked"));
    }
  }
  const playChime = createRedBookChime({ AudioConstructor: RejectedAudio });
  assert.equal(playChime(pinEvent("blocked-event")), true);
  await new Promise(resolve => setImmediate(resolve));

  class ThrowingAudio {
    addEventListener() {}

    play() {
      throw new Error("audio unavailable");
    }
  }
  assert.equal(createRedBookChime({ AudioConstructor: ThrowingAudio })(pinEvent("throw-event")), false);
});

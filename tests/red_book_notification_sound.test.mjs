import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { createRedBookSiren } from "../static/red_book_notification_sound.js";

const pinEvent = eventId => ({
  data: {
    title: "Mannat Moon Construction — Red Book",
    event_id: eventId,
  },
});

test("plays the local siren for a Red Book pin event", () => {
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
  const playSiren = createRedBookSiren({ AudioConstructor: FakeAudio });

  assert.equal(playSiren(pinEvent("event-1")), true);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].src, "/static/audio/red-book-siren.wav");
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
  const playSiren = createRedBookSiren({ AudioConstructor: FakeAudio });

  assert.equal(playSiren(pinEvent("event-duplicate")), true);
  assert.equal(playSiren(pinEvent("event-duplicate")), false);
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
  const playSiren = createRedBookSiren({ AudioConstructor: FakeAudio });

  assert.equal(playSiren(pinEvent("event-a")), true);
  assert.equal(playSiren(pinEvent("event-b")), true);
  assert.equal(playCount, 2);
});

test("does not play the siren for unrelated notifications or missing event IDs", () => {
  let playCount = 0;
  class FakeAudio {
    play() {
      playCount += 1;
      return Promise.resolve();
    }
  }
  const playSiren = createRedBookSiren({ AudioConstructor: FakeAudio });

  assert.equal(playSiren({ data: { title: "Other notification", event_id: "event-2" } }), false);
  assert.equal(playSiren({ data: { title: "Mannat Moon Construction — Red Book" } }), false);
  assert.equal(playCount, 0);
});

test("playback rejection and synchronous audio failures do not escape", async () => {
  class RejectedAudio {
    addEventListener() {}

    play() {
      return Promise.reject(new Error("autoplay blocked"));
    }
  }
  const playSiren = createRedBookSiren({ AudioConstructor: RejectedAudio });
  assert.equal(playSiren(pinEvent("blocked-event")), true);
  await new Promise(resolve => setImmediate(resolve));

  class ThrowingAudio {
    addEventListener() {}

    play() {
      throw new Error("audio unavailable");
    }
  }
  assert.equal(createRedBookSiren({ AudioConstructor: ThrowingAudio })(pinEvent("throw-event")), false);
});

test("siren asset is a short PCM WAV suitable for modern browsers", async () => {
  const asset = await readFile(new URL("../static/audio/red-book-siren.wav", import.meta.url));
  assert.equal(asset.toString("ascii", 0, 4), "RIFF");
  assert.equal(asset.toString("ascii", 8, 12), "WAVE");
  assert.equal(asset.readUInt16LE(20), 1);
  assert.equal(asset.readUInt16LE(22), 1);
  assert.equal(asset.readUInt16LE(34), 16);
  const durationSeconds = asset.readUInt32LE(24) > 0
    ? asset.readUInt32LE(40) / asset.readUInt32LE(28)
    : 0;
  assert.ok(durationSeconds >= 1 && durationSeconds <= 2);
});

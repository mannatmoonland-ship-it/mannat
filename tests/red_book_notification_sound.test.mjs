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

test("plays the local siren for a Red Book pin event", async () => {
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

  assert.equal(await playSiren(pinEvent("event-1")), true);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].src, "/static/audio/red-book-siren.wav");
  assert.deepEqual(calls[1], { played: true });
});

test("does not play again for the same event ID", async () => {
  let playCount = 0;
  class FakeAudio {
    addEventListener() {}

    play() {
      playCount += 1;
      return Promise.resolve();
    }
  }
  const playSiren = createRedBookSiren({ AudioConstructor: FakeAudio });

  assert.equal(await playSiren(pinEvent("event-duplicate")), true);
  assert.equal(await playSiren(pinEvent("event-duplicate")), false);
  assert.equal(playCount, 1);
});

test("plays once for each distinct Red Book event", async () => {
  let playCount = 0;
  let audioCount = 0;
  class FakeAudio {
    constructor(src) {
      audioCount += 1;
      this.src = src;
    }

    addEventListener() {}

    play() {
      playCount += 1;
      return Promise.resolve();
    }
  }
  const playSiren = createRedBookSiren({ AudioConstructor: FakeAudio });

  assert.equal(await playSiren(pinEvent("event-a")), true);
  assert.equal(await playSiren(pinEvent("event-b")), true);
  assert.equal(playCount, 2);
  assert.equal(audioCount, 1);
});

test("test playback unlocks and reuses the audio element for notifications", async () => {
  const calls = [];
  class FakeAudio {
    constructor(src) {
      calls.push({ src });
    }

    play() {
      calls.push({ played: true });
      return Promise.resolve();
    }
  }
  const playSiren = createRedBookSiren({ AudioConstructor: FakeAudio });

  assert.equal(await playSiren.test(), true);
  assert.equal(await playSiren(pinEvent("after-test")), true);
  assert.equal(calls.filter(call => call.src).length, 1);
  assert.equal(calls.filter(call => call.played).length, 2);
});

test("does not play the siren for unrelated notifications or missing event IDs", async () => {
  let playCount = 0;
  class FakeAudio {
    play() {
      playCount += 1;
      return Promise.resolve();
    }
  }
  const playSiren = createRedBookSiren({ AudioConstructor: FakeAudio });

  assert.equal(await playSiren({ data: { title: "Other notification", event_id: "event-2" } }), false);
  assert.equal(await playSiren({ data: { title: "Mannat Moon Construction — Red Book" } }), false);
  assert.equal(playCount, 0);
});

test("playback rejection and synchronous audio failures are reported", async () => {
  class RejectedAudio {
    play() {
      return Promise.reject(new Error("autoplay blocked"));
    }
  }
  const playSiren = createRedBookSiren({ AudioConstructor: RejectedAudio });
  await assert.rejects(playSiren(pinEvent("blocked-event")), /autoplay blocked/);

  class ThrowingAudio {
    play() {
      throw new Error("audio unavailable");
    }
  }
  await assert.rejects(
    createRedBookSiren({ AudioConstructor: ThrowingAudio })(pinEvent("throw-event")),
    /audio unavailable/,
  );
});

test("test playback rejects clearly when the siren asset cannot be loaded or played", async () => {
  class FailedAudio {
    constructor(src) {
      this.src = src;
    }

    play() {
      return Promise.reject(Object.assign(new Error("media decode failed"), { name: "NotSupportedError" }));
    }
  }
  const playSiren = createRedBookSiren({ AudioConstructor: FailedAudio });

  await assert.rejects(playSiren.test(), error => error.name === "NotSupportedError");
  await assert.rejects(playSiren({ data: { title: "Mannat Moon Construction — Red Book", event_id: "failed-play" } }), error => error.name === "NotSupportedError");
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

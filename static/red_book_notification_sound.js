const RED_BOOK_NOTIFICATION_TITLE = "Mannat Moon Construction — Red Book";

export function createRedBookSiren({
  AudioConstructor = globalThis.Audio,
} = {}) {
  const recentEvents = new Set();
  let audio;

  async function playAudio() {
    if (typeof AudioConstructor !== "function") {
      throw new Error("Audio playback is not available in this browser.");
    }
    if (!audio) {
      audio = new AudioConstructor("/static/audio/red-book-siren.wav");
      audio.preload = "auto";
    }
    audio.currentTime = 0;
    await audio.play();
    return true;
  }

  const playSiren = async payload => {
    const data = payload?.data || {};
    const eventId = data.event_id;
    if (data.title !== RED_BOOK_NOTIFICATION_TITLE || typeof eventId !== "string" || !eventId) {
      return false;
    }
    if (recentEvents.has(eventId)) return false;

    recentEvents.add(eventId);
    return playAudio();
  };

  playSiren.test = playAudio;
  return playSiren;
}

const RED_BOOK_NOTIFICATION_TITLE = "Mannat Moon Construction — Red Book";

export function createRedBookChime({
  AudioConstructor = globalThis.Audio,
} = {}) {
  const recentEvents = new Set();
  const activeAudio = new Set();

  return payload => {
    const data = payload?.data || {};
    const eventId = data.event_id;
    if (data.title !== RED_BOOK_NOTIFICATION_TITLE || typeof eventId !== "string" || !eventId) {
      return false;
    }
    if (recentEvents.has(eventId)) return false;

    recentEvents.add(eventId);

    let audio;
    try {
      if (typeof AudioConstructor !== "function") return false;
      audio = new AudioConstructor("/static/audio/red-book-chime.wav");
      audio.preload = "auto";
      activeAudio.add(audio);
      const releaseAudio = () => activeAudio.delete(audio);
      audio.addEventListener("ended", releaseAudio, { once: true });
      audio.addEventListener("error", releaseAudio, { once: true });
      const playback = audio.play();
      if (playback && typeof playback.catch === "function") {
        playback.catch(releaseAudio);
      }
      return true;
    } catch {
      if (audio) activeAudio.delete(audio);
      return false;
    }
  };
}

const RED_BOOK_NOTIFICATION_TITLE = "Mannat Moon Construction — Red Book";

function logError(logger, level, message, error) {
  logger[level](message, { name: error?.name || "Error" });
}

export function createRedBookForegroundNotificationHandler({
  NotificationApi = globalThis.Notification,
  getRegistration = () => undefined,
  playSiren,
  onSirenFailure = () => {},
  windowApi = globalThis.window,
  logger = globalThis.console,
} = {}) {
  const handledEventIds = new Set();

  function attemptSiren(payload) {
    try {
      Promise.resolve(playSiren(payload)).then(
        played => {
          if (!played) {
            logger.warn("Red Book siren playback did not start.");
            onSirenFailure();
          }
        },
        error => {
          logError(logger, "warn", "Red Book siren playback failed.", error);
          onSirenFailure(error);
        },
      );
    } catch (error) {
      logError(logger, "warn", "Red Book siren playback failed.", error);
      onSirenFailure(error);
    }
  }

  return async payload => {
    const data = payload?.data;
    const eventId = data?.event_id;
    if (
      data?.title !== RED_BOOK_NOTIFICATION_TITLE
      || typeof eventId !== "string"
      || !eventId
    ) {
      logger.info("Ignored a foreground message that is not a Red Book pin event.");
      return false;
    }
    if (handledEventIds.has(eventId)) return false;
    if (!NotificationApi || NotificationApi.permission !== "granted") {
      logger.warn("Red Book notification was not displayed because browser permission is not granted.");
      return false;
    }

    handledEventIds.add(eventId);
    const targetUrl = (() => {
      try {
        const target = new URL(data.url || "/red-book", windowApi.location.origin);
        return target.origin === windowApi.location.origin ? target.href : "/red-book";
      } catch {
        return "/red-book";
      }
    })();
    const options = {
      body: typeof data.body === "string" && data.body
        ? data.body
        : "There is a new Red Book update.",
      tag: eventId,
      renotify: false,
      data: { url: targetUrl },
    };

    let displayPromise;
    let displayError;
    const registration = getRegistration();
    try {
      if (registration?.showNotification) {
        displayPromise = Promise.resolve(
          registration.showNotification(data.title, options),
        );
      } else {
        const notification = new NotificationApi(data.title, options);
        notification.onclick = () => {
          windowApi.focus();
          windowApi.location.assign(targetUrl);
        };
        displayPromise = Promise.resolve();
      }
    } catch (error) {
      displayError = error;
      displayPromise = Promise.reject(error);
    }

    attemptSiren(payload);

    try {
      await displayPromise;
      return true;
    } catch (error) {
      const failure = displayError || error;
      if (!registration?.showNotification) {
        logError(logger, "error", "Red Book notification display failed.", failure);
        throw failure;
      }
      logError(logger, "warn", "Service worker notification display failed; trying the page notification API.", failure);
      try {
        const notification = new NotificationApi(data.title, options);
        notification.onclick = () => {
          windowApi.focus();
          windowApi.location.assign(targetUrl);
        };
        return true;
      } catch (fallbackError) {
        logError(logger, "error", "Red Book page notification fallback failed.", fallbackError);
        throw fallbackError;
      }
    }
  };
}

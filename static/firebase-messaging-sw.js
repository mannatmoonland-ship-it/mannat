importScripts(
  "https://www.gstatic.com/firebasejs/12.2.1/firebase-app-compat.js",
  "https://www.gstatic.com/firebasejs/12.2.1/firebase-messaging-compat.js",
);

firebase.initializeApp(self.MANNAT_FIREBASE_CONFIG);
const messaging = firebase.messaging();

messaging.onBackgroundMessage(payload => {
  const data = payload.data || {};
  if (
    data.title !== "Mannat Moon Construction — Red Book"
    || typeof data.event_id !== "string"
    || !data.event_id
  ) {
    console.info("Ignored a background message that is not a Red Book pin event.");
    return;
  }
  return self.registration.showNotification(data.title, {
    body: data.body || "There is a new Red Book update.",
    data: {
      url: data.url || "/red-book",
      eventId: data.event_id || "",
    },
    tag: data.event_id || undefined,
    renotify: false,
  }).catch(error => {
    console.error("Red Book background notification display failed.", {
      name: error?.name || "Error",
    });
    throw error;
  });
});

self.addEventListener("notificationclick", event => {
  event.stopImmediatePropagation();
  event.notification.close();
  const target = new URL(event.notification.data?.url || "/red-book", self.location.origin);
  if (target.origin !== self.location.origin) return;

  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of windows) {
      if (typeof client.navigate === "function") await client.navigate(target.href);
      return client.focus();
    }
    return self.clients.openWindow(target.href);
  })());
});

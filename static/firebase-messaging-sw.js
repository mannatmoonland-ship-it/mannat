importScripts(
  "https://www.gstatic.com/firebasejs/12.2.1/firebase-app-compat.js",
  "https://www.gstatic.com/firebasejs/12.2.1/firebase-messaging-compat.js",
);

firebase.initializeApp(self.MANNAT_FIREBASE_CONFIG);
const messaging = firebase.messaging();

messaging.onBackgroundMessage(payload => {
  const data = payload.data || {};
  const title = data.title || "Mannat Moon Construction";
  const body = data.body || "There is a new Red Book update.";
  self.registration.showNotification(title, {
    body,
    data: {
      url: data.url || "/red-book",
      eventId: data.event_id || "",
    },
    tag: data.event_id || undefined,
    renotify: false,
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

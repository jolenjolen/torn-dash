// Torn Companion service worker: shows reminders sent by the server, even with the tab closed.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));

self.addEventListener("push", event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = {title: "Torn Companion", body: event.data && event.data.text()}; }
  event.waitUntil(self.registration.showNotification(data.title || "Torn Companion", {
    body: data.body || "",
    tag: data.tag || undefined,
    icon: "/static/icon-192.png",
    badge: "/static/icon-192.png",
    data: {url: data.url || "/"},
  }));
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil((async () => {
    // Torn links open in a new tab; dashboard links focus an open dashboard if there is one.
    if (/^https?:\/\//.test(url) && !url.startsWith(self.location.origin)) return self.clients.openWindow(url);
    const wins = await self.clients.matchAll({type: "window", includeUncontrolled: true});
    for (const w of wins) { if ("focus" in w) return w.focus(); }
    return self.clients.openWindow(url);
  })());
});

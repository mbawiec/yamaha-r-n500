const CACHE = "yamaha-v5";
const PRECACHE = ["/", "/static/manifest.json", "/static/sw.js", "/static/icon-192.png", "/static/icon-512.png", "/static/amplifier.png", "/static/favicon-32.png", "/static/favicon-48.png"];

self.addEventListener("install", e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", e => {
  // Skip non-GET, API calls and WebSocket
  if (e.request.method !== "GET") return;
  const url = e.request.url;
  if (url.includes("/api/") || url.includes("/ws")) return;

  e.respondWith(
    fetch(e.request)
      .then(r => {
        const clone = r.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return r;
      })
      .catch(() => caches.match(e.request))
  );
});

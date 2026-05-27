// Lunenburg Events service worker
// Bump CACHE_VERSION on every deploy so clients pick up new assets.

const CACHE_VERSION = "lunapp-v11";
const STATIC_CACHE = `lunenburg-static-${CACHE_VERSION}`;
const DATA_CACHE = `lunenburg-data-${CACHE_VERSION}`;

const PRECACHE_URLS = [
  "./",
  "./index.html",
  "./style.css",
  "./app.js",
  "./manifest.json"
];

// Best-effort runtime cache targets — missing files won't fail install.
const RUNTIME_ASSETS = [
  "./assets/banner_lunenburg.png",
  "./assets/icon-192.png",
  "./assets/icon-512.png"
];

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(STATIC_CACHE);
    await cache.addAll(PRECACHE_URLS);
    // Best-effort: don't fail install if these aren't there yet.
    await Promise.all(RUNTIME_ASSETS.map(url =>
      fetch(url).then(r => r.ok && cache.put(url, r.clone())).catch(() => {})
    ));
    self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(
      names
        .filter(n => n !== STATIC_CACHE && n !== DATA_CACHE)
        .map(n => caches.delete(n))
    );
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Network-first for the data file (always try fresh).
  if (url.pathname.endsWith("/events.json") || url.pathname.endsWith("events.json")) {
    event.respondWith(networkFirst(req, DATA_CACHE));
    return;
  }

  // Cache-first for everything else (static assets, HTML).
  event.respondWith(cacheFirst(req, STATIC_CACHE));
});

async function cacheFirst(req, cacheName) {
  const cached = await caches.match(req);
  if (cached) return cached;
  try {
    const resp = await fetch(req);
    if (resp.ok) {
      const cache = await caches.open(cacheName);
      cache.put(req, resp.clone());
    }
    return resp;
  } catch {
    const fallback = await caches.match("./index.html");
    return fallback || Response.error();
  }
}

async function networkFirst(req, cacheName) {
  try {
    const resp = await fetch(req);
    if (resp.ok) {
      const cache = await caches.open(cacheName);
      cache.put(req, resp.clone());
    }
    return resp;
  } catch {
    const cached = await caches.match(req);
    if (cached) return cached;
    return Response.error();
  }
}

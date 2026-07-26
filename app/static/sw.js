/* Service worker. Served from the root so its scope covers the whole app.
 * __VERSION__ is substituted with a hash of the shipped assets, so a new release
 * gets a fresh cache instead of resurrecting the previous one. */
"use strict";

const VERSION = "__VERSION__";
const CACHE = `subremuxer-${VERSION}`;

/** Enough to open the app offline. Everything else is fetched on demand. */
const SHELL = [
  "/",
  "/static/styles.css",
  "/static/app.js",
  "/static/help.js",
  "/static/favicon.svg",
  "/static/icon-192.png",
  "/static/icon-512.png",
  "/manifest.webmanifest",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      // One missing file must not fail the whole install.
      .then((cache) => Promise.allSettled(SHELL.map((url) => cache.add(url))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

/** Paths the worker must never touch. */
function isBypassed(url) {
  return (
    url.pathname.startsWith("/api/") ||
    url.pathname.startsWith("/s/") ||
    url.pathname.startsWith("/probe/") ||
    url.pathname === "/healthz"
  );
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin || isBypassed(url)) return;

  // Static assets carry a content hash in the query, so a cache hit is always
  // the right answer for the version currently deployed.
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(request).then(
        (hit) =>
          hit ||
          fetch(request).then((response) => {
            if (response.ok) {
              const copy = response.clone();
              caches.open(CACHE).then((cache) => cache.put(request, copy));
            }
            return response;
          })
      )
    );
    return;
  }

  // The shell itself must stay fresh; the cache is only a fallback for offline.
  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy));
        }
        return response;
      })
      .catch(() => caches.match(request).then((hit) => hit || caches.match("/")))
  );
});

/* muse service worker — minimal app-shell cache + Web Push.
 *
 * Deliberately NEVER touches /api/* (API + SSE auth must hit the network), and
 * never caches auth responses. Built JS/CSS are content-hashed, so we cache them
 * opportunistically (cache-first) and let new hashes populate naturally; the SPA
 * shell ("/") is network-first so a deploy is picked up, with an offline fallback.
 */
const CACHE = "muse-shell-v2";
const SHELL = ["/", "/manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Never intercept API / SSE / auth — let the browser handle them (with cookies).
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/mcp")) return;

  // The shell ("/") is ALWAYS network-first — navigations AND programmatic
  // fetches (the new-build check) — so a cached copy never shadows the live
  // index.html. Falls back to the cached shell when offline. Never writes to
  // cache here, so cache-busted checks don't pollute it.
  if (request.mode === "navigate" || url.pathname === "/") {
    event.respondWith(
      fetch(request).catch(() => caches.match("/", { ignoreSearch: true })),
    );
    return;
  }

  // Static assets: cache-first, then populate the cache on first network hit.
  event.respondWith(
    caches.match(request).then(
      (hit) =>
        hit ||
        fetch(request).then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(request, copy));
          }
          return res;
        }),
    ),
  );
});

// --- Web Push -------------------------------------------------------------
self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { title: "muse", body: event.data ? event.data.text() : "" };
  }
  const title = data.title || "muse";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: data.body || "",
      tag: data.tag || undefined,
      data: { url: data.url || "/" },
      icon: "/icons/icon-192.png",
      badge: "/icons/icon-192.png",
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
      const dest = new URL(target, self.location.origin);
      for (const w of wins) {
        if (!("focus" in w)) continue;
        // Already on the target page (e.g. mid-reply on that very session) — just
        // focus it. Navigating would reload the page and wipe whatever's typed.
        const here = new URL(w.url);
        if (here.pathname + here.search === dest.pathname + dest.search) return w.focus();
        if ("navigate" in w) return w.navigate(target).then((c) => (c || w).focus());
        return w.focus();
      }
      return self.clients.openWindow(target);
    }),
  );
});

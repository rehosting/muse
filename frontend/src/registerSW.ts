import { api } from "./api/client";

/** Register the service worker (production only — dev uses Vite's HMR server). */
export function registerSW(): void {
  if (!("serviceWorker" in navigator)) return;
  if (!import.meta.env.PROD) return;
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      /* registration failures are non-fatal — the app still works online */
    });
  });
  watchForNewBuild();
}

/** Notice when a new build is deployed and OFFER to load it — never reload on
 * our own. A standalone PWA that's merely foregrounded never NAVIGATES, so the
 * service worker's network-first shell fetch doesn't run and the old bundle can
 * linger. But force-reloading on focus destroys whatever the user was typing, so
 * instead we show a dismissible "Update" pill; tapping it reloads when the user
 * is ready. Whenever the app regains focus (and every few minutes) we compare
 * the live index.html's hashed entry script to the one we booted with. */
function watchForNewBuild(): void {
  const running = document
    .querySelector<HTMLScriptElement>('script[src*="/assets/index-"]')
    ?.getAttribute("src")
    ?.match(/index-[\w-]+\.js/)?.[0];
  if (!running) return;
  let checking = false;
  let offered = false;
  const check = async () => {
    if (checking || offered || document.hidden) return;
    checking = true;
    try {
      // Cache-bust the query so even a stale service worker (which serves "/"
      // cache-first for non-navigation fetches) is forced to the network — else
      // we'd compare against a frozen shell and cry "new version" forever.
      const res = await fetch(`/?_=${Date.now()}`, { cache: "no-store" });
      if (!res.ok) return;
      const html = await res.text();
      const live = html.match(/index-[\w-]+\.js/)?.[0];
      if (live && live !== running) {
        offered = true;
        showUpdatePill();
      }
    } catch {
      /* offline — try again later */
    } finally {
      checking = false;
    }
  };
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) check();
  });
  window.setInterval(check, 5 * 60 * 1000);
}

/** A tap-to-reload pill, injected once. Deliberately NOT auto-dismissing and NOT
 * auto-reloading — the user reloads on their terms so in-progress input survives. */
function showUpdatePill(): void {
  if (document.getElementById("muse-update-pill")) return;
  const btn = document.createElement("button");
  btn.id = "muse-update-pill";
  btn.className = "muse-update-pill";
  btn.textContent = "↻ New version — tap to update";
  btn.addEventListener("click", () => window.location.reload());
  document.body.appendChild(btn);
}

function urlBase64ToUint8Array(base64: string): BufferSource {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const b64 = (base64 + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(b64);
  const arr = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
  return arr;
}

/**
 * Ask for notification permission, subscribe this device to Web Push using the
 * server's VAPID public key, and register the subscription with muse. Returns a
 * status string for the UI. Must run inside the installed PWA on iOS.
 */
export async function subscribeToPush(): Promise<"subscribed" | "denied" | "unsupported"> {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return "unsupported";
  const permission = await Notification.requestPermission();
  if (permission !== "granted") return "denied";

  const reg = await navigator.serviceWorker.ready;
  const { public_key } = await api.getVapidKey();
  let sub = await reg.pushManager.getSubscription();
  if (!sub) {
    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(public_key),
    });
  }
  const json = sub.toJSON();
  await api.addPushSubscription({
    endpoint: json.endpoint || "",
    keys: { p256dh: json.keys?.p256dh || "", auth: json.keys?.auth || "" },
    label: navigator.userAgent.slice(0, 80),
  });
  return "subscribed";
}

/** Tear down this device's push subscription (browser + server). */
export async function unsubscribeFromPush(): Promise<void> {
  if (!("serviceWorker" in navigator)) return;
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  if (sub) {
    await api.removePushSubscription(sub.endpoint).catch(() => {});
    await sub.unsubscribe().catch(() => {});
  }
}

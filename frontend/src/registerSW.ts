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

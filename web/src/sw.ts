/// <reference lib="webworker" />
import { listDrafts, readShare, saveDraft } from "./share";

const worker = self as unknown as ServiceWorkerGlobalScope;
worker.addEventListener("install", (event) =>
  event.waitUntil(worker.skipWaiting()),
);
worker.addEventListener("activate", (event) => {
  event.waitUntil(
    Promise.all([worker.clients.claim(), listDrafts().catch(() => {})]),
  );
});

worker.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (
    url.origin !== worker.location.origin ||
    url.pathname !== "/share-target" ||
    event.request.method !== "POST" ||
    event.request.mode !== "navigate"
  )
    return;
  event.respondWith(receive(event.request));
});

async function receive(request: Request): Promise<Response> {
  const next = new URL("/", worker.location.origin);
  try {
    if (worker.location.protocol !== "https:")
      throw new Error("HTTPS required");
    if (
      !["none", "same-origin"].includes(
        request.headers.get("Sec-Fetch-Site") ?? "none",
      )
    )
      throw new Error("Cross-origin share");
    if (
      !request.headers
        .get("Content-Type")
        ?.toLowerCase()
        .startsWith("multipart/form-data;")
    )
      throw new Error("Expected multipart share");
    await saveDraft(readShare(await request.formData()));
  } catch {
    next.searchParams.set("share", "failed");
  }
  return Response.redirect(next, 303);
}

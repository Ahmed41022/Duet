// @vitest-environment jsdom
import { expect, it, vi } from "vitest";

it("removes the QR token before pairing, pairs with it, then sends text from the mobile UI", async () => {
  document.body.innerHTML = '<div id="app"></div>';
  window.history.replaceState({}, "", "/#token=qr-secret");
  localStorage.clear();
  let paired = false;
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      if (url === "/api/session")
        return paired
          ? Response.json({ authenticated: true })
          : new Response("", { status: 401 });
      if (url === "/api/pair") {
        expect(JSON.parse(String(init?.body))).toEqual({ token: "qr-secret" });
        paired = true;
        return new Response(null, { status: 204 });
      }
      if (url === "/api/text" && init?.method === "POST")
        return new Response(null, { status: 201 });
      if (url === "/api/text")
        return Response.json({
          messages: [
            {
              id: "m1",
              text: "from PC",
              source: "PC",
              created_at: "2026-01-01T12:00:00Z",
            },
          ],
        });
      if (url === "/api/outbox") return new Response("", { status: 404 });
      return new Response("", { status: 404 });
    }),
  );
  await import("./main");
  expect(location.hash, document.body.innerHTML).toBe("");
  await new Promise((r) => setTimeout(r, 10));
  expect(calls.some((c) => c.url === "/api/pair")).toBe(true);
  expect(document.querySelector("#pairForm")).toBeNull();
  expect(
    document.querySelector("#textForm"),
    document.body.innerHTML,
  ).toBeTruthy();
  const area = document.querySelector<HTMLTextAreaElement>(
    'textarea[name="text"]',
  )!;
  area.value = "from phone";
  document
    .querySelector<HTMLFormElement>("#textForm")!
    .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  await new Promise((r) => setTimeout(r, 0));
  expect(
    calls.some(
      (c) =>
        c.url === "/api/text" &&
        c.init?.method === "POST" &&
        JSON.parse(String(c.init.body)).text === "from phone",
    ),
  ).toBe(true);
  expect(document.querySelector(".message")?.textContent).toContain("from PC");
  vi.unstubAllGlobals();
});

it("pairs with a PIN and keeps an unsent text draft while the language changes", async () => {
  vi.resetModules();
  document.body.innerHTML = '<div id="app"></div>';
  window.history.replaceState({}, "", "/");
  localStorage.clear();
  let paired = false;
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      if (url === "/api/pair") {
        expect(JSON.parse(String(init?.body))).toEqual({ pin: "123456" });
        paired = true;
        return new Response(null, { status: 204 });
      }
      if (url === "/api/session")
        return paired
          ? Response.json({ authenticated: true })
          : new Response(null, { status: 401 });
      if (url === "/api/text") return Response.json({ messages: [] });
      if (url === "/api/outbox") return new Response(null, { status: 404 });
      return new Response(null, { status: 404 });
    }),
  );
  await import("./main");
  const pin = document.querySelector<HTMLInputElement>("#pin")!;
  pin.value = "123456";
  document
    .querySelector<HTMLFormElement>("#pairForm")!
    .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  await new Promise((r) => setTimeout(r, 10));
  const textarea = document.querySelector<HTMLTextAreaElement>("#share-text")!;
  textarea.value = "draft stays here";
  document.querySelector<HTMLButtonElement>("#lang")!.click();
  expect(document.documentElement.dir).toBe("rtl");
  expect(document.documentElement.lang).toBe("ar");
  expect(
    document.querySelector<HTMLTextAreaElement>("#share-text")!.value,
  ).toBe("draft stays here");
  expect(calls.some((call) => call.url === "/api/pair")).toBe(true);
  vi.unstubAllGlobals();
});

it("keeps a cancelled upload visible until its server reservation is deleted", async () => {
  vi.resetModules();
  document.body.innerHTML = '<div id="app"></div>';
  window.history.replaceState({}, "", "/");
  localStorage.clear();
  localStorage.setItem(
    "duet.uploads.v1",
    JSON.stringify([
      {
        key: "saved",
        name: "large.bin",
        size: 10,
        uploadId: "upload-1",
        chunkSize: 4,
        status: "uploading",
      },
    ]),
  );
  let deletes = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/session") return Response.json({ authenticated: true });
      if (url === "/api/text") return Response.json({ messages: [] });
      if (url === "/api/outbox") return new Response(null, { status: 404 });
      if (url === "/api/uploads/upload-1" && init?.method === "DELETE") {
        deletes += 1;
        return new Response(null, { status: deletes === 1 ? 503 : 204 });
      }
      return new Response(null, { status: 404 });
    }),
  );
  await import("./main");
  await new Promise((resolve) => setTimeout(resolve, 10));
  document
    .querySelector<HTMLButtonElement>('.remove[data-key="saved"]')!
    .click();
  await new Promise((resolve) => setTimeout(resolve, 10));
  expect(document.querySelector(".transfer")).toBeTruthy();
  expect(document.querySelector(".resume")?.textContent).toBe("Retry cleanup");
  document.querySelector<HTMLButtonElement>(".resume")!.click();
  await new Promise((resolve) => setTimeout(resolve, 10));
  expect(deletes).toBe(2);
  expect(document.querySelector(".transfer")).toBeNull();
  vi.unstubAllGlobals();
});

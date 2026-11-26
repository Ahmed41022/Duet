// @vitest-environment jsdom
// @vitest-environment-options {"url":"https://duet.test/"}
import { expect, it, vi } from "vitest";
import { clearDraftText, deleteDraft, listDrafts } from "./share";

vi.mock("./share", () => ({
  listDrafts: vi.fn(),
  clearDraftText: vi.fn(),
  deleteDraft: vi.fn(),
}));

it("waits for pairing and a separate send action, and copies message text on demand", async () => {
  document.body.innerHTML = '<div id="app"></div>';
  localStorage.clear();
  let paired = false;
  let drafts = [
    {
      id: "first",
      text: "<Shared link>",
      files: [],
      expiresAt: Date.now() + 60000,
    },
    {
      id: "second",
      text: "Discard me",
      files: [],
      expiresAt: Date.now() + 60000,
    },
  ];
  vi.mocked(listDrafts).mockImplementation(async () => drafts);
  const remove = async (id: string) => {
    drafts = drafts.filter((draft) => draft.id !== id);
  };
  vi.mocked(clearDraftText).mockImplementation(remove);
  vi.mocked(deleteDraft).mockImplementation(remove);
  const sent: string[] = [];
  const writeText = vi
    .fn()
    .mockResolvedValueOnce(undefined)
    .mockRejectedValueOnce(new Error("denied"));
  vi.stubGlobal("isSecureContext", true);
  vi.stubGlobal("navigator", {
    onLine: true,
    serviceWorker: { register: vi.fn().mockResolvedValue(undefined) },
    locks: { request: (_name: string, task: () => Promise<void>) => task() },
    clipboard: { writeText },
  });
  vi.stubGlobal("indexedDB", {});
  vi.stubGlobal(
    "fetch",
    vi.fn(async (resource: RequestInfo | URL, init?: RequestInit) => {
      switch (String(resource)) {
        case "/api/pair":
          paired = true;
          return new Response(null, { status: 204 });
        case "/api/session":
          return Response.json({ paired }, { status: paired ? 200 : 401 });
        case "/api/text":
          if (init?.method === "POST") {
            sent.push(JSON.parse(String(init.body)).text);
            return new Response(null, { status: 201 });
          }
          return Response.json({
            messages: [
              {
                id: "msg",
                text: "Exact <message>",
                source: "pc",
                created_at: "2026-01-01T12:00:00Z",
              },
            ],
          });
        default:
          return new Response(null, { status: 404 });
      }
    }),
  );
  try {
    await import("./main");
    const sendButton = () =>
      document.querySelector<HTMLButtonElement>(
        '[data-draft="first"] [data-draft-action="send"]',
      )!;
    await vi.waitFor(() =>
      expect(document.querySelector(".share-preview")?.textContent).toBe(
        "<Shared link>",
      ),
    );
    expect(sendButton().disabled).toBe(true);
    document.querySelector<HTMLInputElement>("#pin")!.value = "123456";
    document
      .querySelector("#pairForm")!
      .dispatchEvent(new Event("submit", { cancelable: true }));
    await vi.waitFor(() => expect(sendButton().disabled).toBe(false));
    expect(sent).toEqual([]);
    sendButton().click();
    await vi.waitFor(() =>
      expect(document.querySelector('[data-draft="first"]')).toBeNull(),
    );
    expect(sent).toEqual(["<Shared link>"]);
    document
      .querySelector<HTMLButtonElement>(
        '[data-draft="second"] [data-draft-action="discard"]',
      )!
      .click();
    await vi.waitFor(() =>
      expect(document.querySelector("[data-draft]")).toBeNull(),
    );
    expect(sent).toEqual(["<Shared link>"]);

    expect(writeText).not.toHaveBeenCalled();
    document.querySelector<HTMLButtonElement>(".copy-text")!.click();
    await vi.waitFor(() =>
      expect(document.querySelector("#copy-status")?.textContent).toBe(
        "Copied to clipboard.",
      ),
    );
    expect(writeText).toHaveBeenCalledWith("Exact <message>");
    document.querySelector<HTMLButtonElement>("#lang")!.click();
    expect(document.querySelector(".copy-text")?.textContent).toBe("نسخ");
    document.querySelector<HTMLButtonElement>(".copy-text")!.click();
    await vi.waitFor(() =>
      expect(document.querySelector("#copy-status")?.textContent).toContain(
        "تعذر النسخ",
      ),
    );
  } finally {
    vi.unstubAllGlobals();
  }
});

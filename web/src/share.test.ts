// @vitest-environment node
import "fake-indexeddb/auto";
import { afterEach, expect, it, vi } from "vitest";
import {
  clearDraftText,
  deleteDraft,
  listDrafts,
  readShare,
  saveDraft,
} from "./share";

afterEach(async () => {
  vi.useRealTimers();
  await Promise.all((await listDrafts()).map(({ id }) => deleteDraft(id)));
});

function formWithText(text: string): FormData {
  const form = new FormData();
  form.set("text", text);
  return form;
}

it("accepts multipart files and combines Android text, title and URL fields", () => {
  const form = formWithText(" https://example.test ");
  form.set("title", "Page title");
  form.set("url", "https://example.test");
  form.append("files", new File(["first"], "first.txt"));
  form.append("files", new File(["second"], "second.txt"));

  const draft = readShare(form);
  expect(draft.text).toBe("Page title\nhttps://example.test");
  expect(draft.files.map((file) => file.name)).toEqual([
    "first.txt",
    "second.txt",
  ]);
});

it("rejects empty and oversized text or file-count selections", () => {
  expect(() => readShare(formWithText(" "))).toThrow("Empty share");
  expect(() => readShare(formWithText("a".repeat(10_001)))).toThrow(
    "Share is too large",
  );
  const form = formWithText("files");
  for (let index = 0; index < 21; index++)
    form.append("files", new File(["a"], `file-${index}.txt`));
  expect(() => readShare(form)).toThrow("Share is too large");
});

it("stores independent drafts through reload and prunes them at their expiry", async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  const first = readShare(formWithText("first"));
  await saveDraft(first);
  vi.setSystemTime(Date.now() + 1000);
  const second = readShare(formWithText("second"));
  await saveDraft(second);
  expect(await listDrafts()).toEqual([second, first]);

  // Re-importing the page's storage module represents a reload with the same database.
  vi.resetModules();
  const reloaded = await import("./share");
  expect(await reloaded.listDrafts()).toHaveLength(2);
  vi.setSystemTime(first.expiresAt);
  expect(await reloaded.listDrafts()).toEqual([second]);
  await deleteDraft(second.id);
  expect(await reloaded.listDrafts()).toEqual([]);
});

it("refuses an extra draft without displacing earlier pending contents", async () => {
  const pending = Array.from({ length: 6 }, (_, index) =>
    readShare(formWithText(`message ${index}`)),
  );
  const initial = pending.slice(0, 4);
  await Promise.all(initial.map(saveDraft));
  const saved = await Promise.allSettled(pending.slice(4).map(saveDraft));
  expect(saved.filter((result) => result.status === "fulfilled")).toHaveLength(
    1,
  );
  const remaining = await listDrafts();
  expect(remaining).toHaveLength(5);
  expect(remaining).toEqual(expect.arrayContaining(initial));
});

it("removes sent text while retaining any files awaiting enqueue", async () => {
  const form = formWithText("sent text");
  form.append("files", new File(["pending"], "sample.txt"));
  const draft = readShare(form);
  await saveDraft(draft);
  await clearDraftText(draft.id);
  const [remaining] = await listDrafts();
  expect(remaining.text).toBe("");
  expect(remaining.files[0].size).toBe(7);
  await deleteDraft(draft.id);
  expect(await listDrafts()).toHaveLength(0);
});

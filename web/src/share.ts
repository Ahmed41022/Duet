const DRAFT_LIFETIME = 60 * 60 * 1000;
const MAX_FILES = 20;
const MAX_DRAFTS = 5;
const MAX_BYTES = 100 * 1024 * 1024;
const MAX_TEXT = 10_000;
const STORE = "drafts";

export type ShareDraft = {
  id: string;
  expiresAt: number;
  text: string;
  files: File[];
};

export function readShare(form: FormData): ShareDraft {
  const files = form
    .getAll("files")
    .filter(
      (value): value is File => typeof value !== "string" && value.name !== "",
    );
  const text = [
    ...new Set(
      ["title", "text", "url"]
        .map((field) => form.get(field))
        .filter((value): value is string => typeof value === "string")
        .map((value) => value.trim())
        .filter(Boolean),
    ),
  ].join("\n");
  if (!text && !files.length) throw new Error("Empty share");
  if (
    text.length > MAX_TEXT ||
    files.length > MAX_FILES ||
    size(files) > MAX_BYTES
  )
    throw new Error("Share is too large");
  return {
    id: crypto.randomUUID(),
    expiresAt: Date.now() + DRAFT_LIFETIME,
    text,
    files,
  };
}

function size(files: File[]): number {
  return files.reduce((sum, file) => sum + file.size, 0);
}

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("duet.share.v1", 1);
    request.onupgradeneeded = () =>
      request.result.createObjectStore(STORE, { keyPath: "id" });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function withDrafts<T>(
  action: (drafts: ShareDraft[], store: IDBObjectStore) => T,
): Promise<T> {
  const database = await openDatabase();
  try {
    return await new Promise((resolve, reject) => {
      const transaction = database.transaction(STORE, "readwrite");
      const store = transaction.objectStore(STORE);
      const request = store.getAll();
      let result: T;
      transaction.oncomplete = () => resolve(result);
      transaction.onabort = () =>
        reject(transaction.error ?? new Error("Drafts unavailable"));
      request.onsuccess = () => {
        const now = Date.now();
        const drafts: ShareDraft[] = [];
        for (const draft of request.result as ShareDraft[]) {
          if (draft.expiresAt <= now) store.delete(draft.id);
          else drafts.push(draft);
        }
        try {
          result = action(drafts, store);
        } catch (error) {
          reject(error);
          transaction.abort();
        }
      };
    });
  } finally {
    database.close();
  }
}

export function listDrafts(): Promise<ShareDraft[]> {
  return withDrafts((drafts) =>
    drafts.sort((a, b) => b.expiresAt - a.expiresAt),
  );
}

export async function saveDraft(draft: ShareDraft): Promise<void> {
  const stored = await withDrafts((drafts, store) => {
    const bytes =
      size(draft.files) +
      drafts.reduce((sum, pending) => sum + size(pending.files), 0);
    if (drafts.length >= MAX_DRAFTS || bytes > MAX_BYTES) return false;
    store.add(draft);
    return true;
  });
  if (!stored) throw new Error("Draft inbox is full");
}

export function deleteDraft(id: string): Promise<void> {
  return withDrafts((_, store) => {
    store.delete(id);
  });
}

export function clearDraftText(id: string): Promise<void> {
  return withDrafts((drafts, store) => {
    const draft = drafts.find((item) => item.id === id);
    if (!draft) return;
    if (draft.files.length) store.put({ ...draft, text: "" });
    else store.delete(id);
  });
}

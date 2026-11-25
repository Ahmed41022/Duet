import { sha256 } from "@noble/hashes/sha2.js";
import { bytesToHex } from "@noble/hashes/utils.js";

export type UploadRecord = {
  key: string;
  name: string;
  size: number;
  relpath?: string;
  uploadId?: string;
  chunkSize?: number;
  sha256?: string;
  bytesDone?: number;
  bytesHashed?: number;
  cancelPending?: boolean;
  status: "queued" | "uploading" | "done" | "duplicate" | "error";
  error?: string;
};

const STORE = "duet.uploads.v1";
const validRecord = (x: unknown): x is UploadRecord => {
  if (!x || typeof x !== "object") return false;
  const r = x as Partial<UploadRecord>;
  return (
    typeof r.key === "string" &&
    typeof r.name === "string" &&
    typeof r.size === "number" &&
    r.size >= 0 &&
    (r.uploadId === undefined || typeof r.uploadId === "string") &&
    (r.chunkSize === undefined ||
      (Number.isInteger(r.chunkSize) && r.chunkSize > 0)) &&
    (r.uploadId === undefined ||
      (typeof r.chunkSize === "number" &&
        Number.isInteger(r.chunkSize) &&
        r.chunkSize > 0)) &&
    (r.sha256 === undefined || typeof r.sha256 === "string") &&
    (r.cancelPending === undefined || typeof r.cancelPending === "boolean") &&
    ["queued", "uploading", "done", "duplicate", "error"].includes(
      String(r.status),
    )
  );
};

export function loadQueue(storage: Storage = localStorage): UploadRecord[] {
  try {
    const value: unknown = JSON.parse(storage.getItem(STORE) || "[]");
    return Array.isArray(value) ? value.filter(validRecord) : [];
  } catch {
    return [];
  }
}

export function saveQueue(
  items: UploadRecord[],
  storage: Storage = localStorage,
): void {
  storage.setItem(STORE, JSON.stringify(items));
}

export async function hashBlob(
  blob: Blob,
  chunkSize = 1024 * 1024,
  onProgress?: (done: number, total: number) => void,
  signal?: AbortSignal,
): Promise<string> {
  if (!Number.isSafeInteger(chunkSize) || chunkSize <= 0)
    throw new RangeError("Invalid hash chunk size");
  const hasher = sha256.create();
  for (let offset = 0; offset < blob.size; offset += chunkSize) {
    signal?.throwIfAborted();
    const end = Math.min(offset + chunkSize, blob.size);
    hasher.update(new Uint8Array(await blob.slice(offset, end).arrayBuffer()));
    onProgress?.(end, blob.size);
  }
  signal?.throwIfAborted();
  return bytesToHex(hasher.digest());
}

export class HttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "HttpError";
  }
}

function isRetryable(error: unknown): boolean {
  if (error instanceof DOMException && error.name === "AbortError")
    return false;
  if (error instanceof HttpError)
    return (
      error.status === 408 ||
      error.status === 425 ||
      error.status === 429 ||
      error.status >= 500
    );
  return true; // fetch network errors have no response status
}

export async function retry<T>(
  work: () => Promise<T>,
  attempts = 5,
  wait = (ms: number) =>
    new Promise<void>((resolve) => setTimeout(resolve, ms)),
  onRetry?: (n: number) => void,
): Promise<T> {
  let error: unknown;
  for (let n = 0; n < attempts; n++) {
    try {
      return await work();
    } catch (caught) {
      error = caught;
      if (n === attempts - 1 || !isRetryable(caught)) break;
      onRetry?.(n + 1);
      await wait(Math.min(8000, 500 * 2 ** n));
    }
  }
  throw error;
}

async function request(
  fetcher: typeof fetch,
  url: string,
  init: RequestInit,
  failure: string,
): Promise<Response> {
  const response = await fetcher(url, init);
  if (!response.ok)
    throw new HttpError(response.status, `${failure} (${response.status}).`);
  return response;
}

export async function runUpload(
  file: File,
  record: UploadRecord,
  signal: AbortSignal,
  update: (record: UploadRecord) => void,
  askDuplicate: (duplicate: {
    relpath: string;
    size: number;
    sha256: string;
  }) => Promise<boolean>,
  fetcher: typeof fetch = fetch,
): Promise<void> {
  const set = (patch: Partial<UploadRecord>): void => {
    Object.assign(record, patch);
    update({ ...record });
  };
  if (file.size !== record.size)
    throw new Error(
      "The selected file has changed size. Choose the original file to resume.",
    );
  set({ status: "uploading", error: undefined, bytesDone: 0, bytesHashed: 0 });

  // Rehash the selected file on every run, including after reload, before trusting saved chunks.
  const digest = await hashBlob(file, 1024 * 1024, (done) => set({ bytesHashed: done }), signal);
  if (record.sha256 && record.sha256 !== digest)
    throw new Error(
      "This is a different file. Choose the original file to resume.",
    );
  set({ sha256: digest, bytesHashed: file.size, bytesDone: 0 });

  if (!record.uploadId) {
    const response = await retry(async () =>
      request(
        fetcher,
        "/api/uploads",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": record.key,
          },
          body: JSON.stringify({
            name: record.name,
            size: record.size,
            ...(record.relpath ? { relpath: record.relpath } : {}),
            sha256: digest,
          }),
          signal,
        },
        "Upload setup failed",
      ),
    );
    const created = (await response.json()) as {
      upload_id: string;
      chunk_size: number;
      duplicate?: { relpath: string; size: number; sha256: string };
    };
    if (
      !created.upload_id ||
      !Number.isInteger(created.chunk_size) ||
      created.chunk_size <= 0
    )
      throw new Error("The server returned invalid upload details.");
    set({ uploadId: created.upload_id, chunkSize: created.chunk_size });
    if (created.duplicate) {
      const sendAgain = await askDuplicate(created.duplicate);
      if (!sendAgain) {
        set({ cancelPending: true });
        await request(
          fetcher,
          `/api/uploads/${created.upload_id}`,
          { method: "DELETE", signal },
          "Could not discard the duplicate upload reservation",
        );
        set({ status: "done", bytesDone: record.size, cancelPending: false });
        return;
      }
    }
  }

  const uploadId = record.uploadId!;
  const chunkSize = record.chunkSize!;
  const stateResponse = await retry(async () =>
    request(
      fetcher,
      `/api/uploads/${uploadId}`,
      { signal },
      "Could not check upload progress",
    ),
  );
  const state = (await stateResponse.json()) as {
    received?: number[];
    received_chunks?: number[];
  };
  const received = new Set<number>(
    state.received ?? state.received_chunks ?? [],
  );
  const chunkCount = Math.ceil(file.size / chunkSize);
  let bytesDone = 0;
  for (const index of received) {
    if (index >= 0 && index < chunkCount)
      bytesDone += Math.min(chunkSize, file.size - index * chunkSize);
  }
  set({ bytesDone });

  for (let index = 0; index < chunkCount; index++) {
    if (signal.aborted)
      throw new DOMException("Upload cancelled", "AbortError");
    if (received.has(index)) continue;
    const part = file.slice(
      index * chunkSize,
      Math.min(file.size, (index + 1) * chunkSize),
    );
    const chunkDigest = await hashBlob(part, undefined, undefined, signal);
    await retry(async () =>
      request(
        fetcher,
        `/api/uploads/${uploadId}/chunks/${index}`,
        {
          method: "PUT",
          headers: {
            "X-Chunk-SHA256": chunkDigest,
            "Content-Type": "application/octet-stream",
          },
          body: part,
          signal,
        },
        `Chunk ${index + 1} failed`,
      ),
    );
    bytesDone += part.size;
    set({ bytesDone });
  }

  await retry(async () =>
    request(
      fetcher,
      `/api/uploads/${uploadId}/complete`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sha256: digest }),
        signal,
      },
      "Could not finish upload",
    ),
  );
  set({ status: "done", bytesDone: file.size });
}

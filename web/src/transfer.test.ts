import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  hashBlob,
  loadQueue,
  runUpload,
  saveQueue,
  retry,
  type UploadRecord,
} from "./transfer";

describe("transfer helpers", () => {
  beforeEach(() => {
    localStorage.clear();
    if (!Blob.prototype.arrayBuffer)
      Object.defineProperty(Blob.prototype, "arrayBuffer", {
        configurable: true,
        value: function (this: Blob) {
          return new Promise<ArrayBuffer>((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result as ArrayBuffer);
            reader.onerror = () => reject(reader.error);
            reader.readAsArrayBuffer(this);
          });
        },
      });
  });
  it("hashes bounded pieces, including empty files, without Web Crypto", async () => {
    const old = globalThis.crypto.subtle;
    Object.defineProperty(globalThis, "crypto", {
      configurable: true,
      value: {
        getRandomValues: globalThis.crypto.getRandomValues.bind(
          globalThis.crypto,
        ),
      },
    });
    expect(await hashBlob(new Blob([]))).toBe(
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    );
    expect(await hashBlob(new Blob(["abc"]), 1)).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
    Object.defineProperty(globalThis, "crypto", {
      configurable: true,
      value: { ...globalThis.crypto, subtle: old },
    });
  });
  it("persists upload identifiers for resume after reload", () => {
    const q: UploadRecord[] = [
      {
        key: "opaque",
        name: "a.bin",
        size: 4,
        uploadId: "server-id",
        chunkSize: 2,
        status: "uploading",
      },
    ];
    saveQueue(q);
    expect(loadQueue()[0]).toMatchObject({
      key: "opaque",
      uploadId: "server-id",
      chunkSize: 2,
    });
  });
  it("retries transient failures with capped exponential backoff", async () => {
    let calls = 0;
    const waits: number[] = [];
    const value = await retry(
      async () => {
        if (calls++ < 3) throw Error("offline");
        return 7;
      },
      5,
      async (n) => {
        waits.push(n);
      },
    );
    expect(value).toBe(7);
    expect(waits).toEqual([500, 1000, 2000]);
  });
  it("resumes by checking received chunks and completes without retransmitting them", async () => {
    const f = new File([new Uint8Array([1, 2, 3, 4, 5])], "five.bin");
    const record: UploadRecord = {
      key: "key-1",
      name: f.name,
      size: f.size,
      status: "queued",
    };
    const calls: string[] = [];
    let createKey: string | null = null;
    const fetcher = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        calls.push(`${init?.method ?? "GET"} ${url}`);
        if (url === "/api/uploads") {
          createKey = new Headers(init?.headers).get("Idempotency-Key");
          return Response.json({ upload_id: "u1", chunk_size: 2 });
        }
        if (url === "/api/uploads/u1") return Response.json({ received: [0] });
        if (url.includes("/chunks/"))
          return new Response(null, { status: 204 });
        if (url.endsWith("/complete"))
          return new Response(null, { status: 204 });
        throw Error(url);
      },
    ) as unknown as typeof fetch;
    await runUpload(
      f,
      record,
      new AbortController().signal,
      () => {},
      async () => false,
      fetcher,
    );
    expect(
      calls
        .filter((x) => x.includes("/chunks/"))
        .map((x) => x.split("/").at(-1)),
    ).toEqual(["1", "2"]);
    expect(record.status).toBe("done");
    expect(createKey).toBe("key-1");
  });
  it("reuses a saved upload ID, rechecks server chunks, and counts resumed bytes", async () => {
    const file = new File([new Uint8Array([1, 2, 3, 4, 5])], "resume.bin");
    const record: UploadRecord = {
      key: "saved-key",
      name: file.name,
      size: file.size,
      uploadId: "saved-upload",
      chunkSize: 2,
      sha256: await hashBlob(file),
      status: "uploading",
    };
    const calls: string[] = [];
    const progress: number[] = [];
    const fetcher = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        calls.push(`${init?.method ?? "GET"} ${url}`);
        if (url === "/api/uploads/saved-upload")
          return Response.json({ received: [0] });
        if (url.includes("/chunks/"))
          return new Response(null, { status: 204 });
        if (url.endsWith("/complete"))
          return new Response(null, { status: 204 });
        throw new Error(`Unexpected request: ${url}`);
      },
    ) as unknown as typeof fetch;
    await runUpload(
      file,
      record,
      new AbortController().signal,
      (updated) => progress.push(updated.bytesDone ?? 0),
      async () => false,
      fetcher,
    );
    expect(calls).not.toContain("POST /api/uploads");
    expect(
      calls
        .filter((call) => call.includes("/chunks/"))
        .map((call) => call.split("/").at(-1)),
    ).toEqual(["1", "2"]);
    expect(progress).toContain(2);
    expect(record.bytesDone).toBe(file.size);
  });
  it("offers the duplicate action and deletes its reservation when skipped", async () => {
    const f = new File(["same"], "same.txt");
    const record: UploadRecord = {
      key: "duplicate-key",
      name: f.name,
      size: f.size,
      status: "queued",
    };
    const calls: string[] = [];
    const fetcher = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push(`${init?.method} ${String(input)}`);
        return String(input) === "/api/uploads"
          ? Response.json({
              upload_id: "dup",
              chunk_size: 8,
              duplicate: { relpath: "same.txt", size: 4, sha256: "x" },
            })
          : new Response(null, { status: 204 });
      },
    ) as unknown as typeof fetch;
    await runUpload(
      f,
      record,
      new AbortController().signal,
      () => {},
      async () => false,
      fetcher,
    );
    expect(calls).toContain("DELETE /api/uploads/dup");
    expect(record.status).toBe("done");
  });
  it("resumes with the persisted upload ID and rechecks file identity", async () => {
    const file = new File(["same"], "same.txt");
    const record: UploadRecord = {
      key: "stable-key",
      name: file.name,
      size: file.size,
      uploadId: "saved-id",
      chunkSize: 4,
      sha256: "wrong-digest",
      status: "uploading",
    };
    const fetcher = vi.fn() as unknown as typeof fetch;
    await expect(
      runUpload(
        file,
        record,
        new AbortController().signal,
        () => {},
        async () => false,
        fetcher,
      ),
    ).rejects.toThrow("different file");
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("does not retry a permanent 4xx response", async () => {
    const file = new File(["x"], "x.txt");
    const fetcher = vi.fn(
      async () => new Response(null, { status: 400 }),
    ) as unknown as typeof fetch;
    await expect(
      runUpload(
        file,
        { key: "fixed", name: file.name, size: file.size, status: "queued" },
        new AbortController().signal,
        () => {},
        async () => false,
        fetcher,
      ),
    ).rejects.toThrow("400");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("completes an empty file without attempting chunk PUTs", async () => {
    const file = new File([], "empty.bin");
    const calls: string[] = [];
    const fetcher = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        calls.push(`${init?.method ?? "GET"} ${url}`);
        if (url === "/api/uploads")
          return Response.json({ upload_id: "empty-upload", chunk_size: 8 });
        if (url === "/api/uploads/empty-upload")
          return Response.json({ received: [] });
        if (url.endsWith("/complete"))
          return new Response(null, { status: 204 });
        throw new Error(`Unexpected request: ${url}`);
      },
    ) as unknown as typeof fetch;
    const record: UploadRecord = {
      key: "empty-key",
      name: file.name,
      size: 0,
      status: "queued",
    };
    await runUpload(
      file,
      record,
      new AbortController().signal,
      () => {},
      async () => false,
      fetcher,
    );
    expect(calls.some((call) => call.includes("/chunks/"))).toBe(false);
    expect(calls.some((call) => call.endsWith("/complete"))).toBe(true);
    expect(record.status).toBe("done");
  });
  it("drops invalid persisted queue entries", () => {
    localStorage.setItem(
      "duet.uploads.v1",
      JSON.stringify([
        { key: 3 },
        { key: "ok", name: "x", size: 1, status: "queued" },
      ]),
    );
    expect(loadQueue()).toHaveLength(1);
    localStorage.setItem("duet.uploads.v1", "not json");
    expect(loadQueue()).toEqual([]);
  });
});

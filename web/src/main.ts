import "./style.css";
import {
  clearDraftText,
  deleteDraft,
  listDrafts,
  type ShareDraft,
} from "./share";
import {
  HttpError,
  loadQueue,
  runUpload,
  saveQueue,
  type UploadRecord,
} from "./transfer";

type Language = "en" | "ar";
const translations = {
  en: {
    title: "Duet",
    pair: "Connect to your PC",
    instruction: "Scan the QR code on your PC, or enter its PIN.",
    pin: "6-digit PIN",
    connect: "Connect",
    files: "Send files to PC",
    pick: "Choose files",
    folder: "Choose folder",
    queue: "Transfer queue",
    received: "Files from your PC",
    text: "Shared text",
    write: "Write a message or link…",
    send: "Send",
    empty: "Nothing here yet",
    online: "Connected",
    offline: "Reconnecting…",
    paired: "You're connected to your PC",
    working: "Preparing transfer…",
    hashing: "Verifying file",
    sent: "Uploaded",
    duplicate: "This file is already on your PC. Send it again?",
    resume: "Resume",
    remove: "Remove",
    removeFailed: "Could not cancel the upload. Retry cleanup.",
    retryCleanup: "Retry cleanup",
    mismatch: "Choose the original file to resume this transfer.",
    badPin: "That PIN or QR link is invalid. Check the PC and try again.",
    connection: "Connection failed. Check your Wi-Fi and try again.",
    waiting: "Waiting",
    done: "Transfer complete",
    folderLabel: "Folder",
    failed: "Transfer failed. Choose Resume to try again.",
    shareDraft: "Shared with Duet",
    sharePair: "Connect to your PC to send this share.",
    shareSend: "Send to PC",
    shareDiscard: "Discard draft",
    shareReceived: "Share added to the transfer queue.",
    shareSent: "Shared text sent.",
    shareError:
      "Could not save the share. The draft inbox may be full or the share too large. Try sharing a smaller selection.",
    shareActionError: "Could not finish. Check your connection and try again.",
    shareUnavailable: "This browser could not open the local share inbox.",
    copyText: "Copy",
    copied: "Copied to clipboard.",
    copyFailed: "Could not copy. Select the message to copy it manually.",
  },
  ar: {
    title: "Duet",
    pair: "الاتصال بالكمبيوتر",
    instruction: "امسح رمز QR على الكمبيوتر أو أدخل رمز PIN.",
    pin: "رمز PIN من 6 أرقام",
    connect: "اتصال",
    files: "إرسال ملفات إلى الكمبيوتر",
    pick: "اختيار ملفات",
    folder: "اختيار مجلد",
    queue: "قائمة النقل",
    received: "ملفات من الكمبيوتر",
    text: "رسائل مشتركة",
    write: "اكتب رسالة أو رابطًا…",
    send: "إرسال",
    empty: "لا يوجد شيء بعد",
    online: "متصل",
    offline: "جارٍ إعادة الاتصال…",
    paired: "اتصلت بالكمبيوتر",
    working: "جارٍ تجهيز النقل…",
    hashing: "التحقق من الملف",
    sent: "تم الرفع",
    duplicate: "هذا الملف موجود بالفعل على الكمبيوتر. هل تريد إرساله مجددًا؟",
    resume: "استئناف",
    remove: "إزالة",
    removeFailed: "تعذر إلغاء الرفع. أعد محاولة التنظيف.",
    retryCleanup: "إعادة محاولة التنظيف",
    mismatch: "اختر الملف الأصلي لاستئناف النقل.",
    badPin: "رمز PIN أو رابط QR غير صالح. تحقق من الكمبيوتر وحاول مجددًا.",
    connection: "تعذر الاتصال. تحقق من شبكة Wi-Fi وحاول مجددًا.",
    waiting: "في الانتظار",
    done: "اكتمل النقل",
    folderLabel: "مجلد",
    failed: "فشل النقل. اختر استئناف للمحاولة مجددًا.",
    shareDraft: "مشاركة مع Duet",
    sharePair: "اتصل بالكمبيوتر لإرسال هذه المشاركة.",
    shareSend: "إرسال إلى الكمبيوتر",
    shareDiscard: "حذف المسودة",
    shareReceived: "أُضيفت الملفات إلى قائمة النقل.",
    shareSent: "أُرسل النص المشترك.",
    shareError:
      "تعذر حفظ المشاركة. قد يكون صندوق المسودات ممتلئًا أو حجم المشاركة كبيرًا. حاول مشاركة عناصر أقل.",
    shareActionError: "تعذر إتمام العملية. تحقق من الاتصال وحاول مجددًا.",
    shareUnavailable: "تعذر على هذا المتصفح فتح صندوق المشاركات على الهاتف.",
    copyText: "نسخ",
    copied: "تم النسخ إلى الحافظة.",
    copyFailed: "تعذر النسخ. حدّد الرسالة لنسخها يدويًا.",
  },
} as const;
type Copy = (typeof translations)[Language];
let language: Language =
  localStorage.getItem("duet.lang") === "ar" ? "ar" : "en";
let copy: Copy = translations[language];
let qrToken =
  new URL(location.href).hash.match(/(?:^#|&)token=([^&]*)/)?.[1] ?? "";
if (qrToken) {
  try {
    qrToken = decodeURIComponent(qrToken);
  } catch {
    /* pairing reports malformed tokens */
  }
  history.replaceState(null, "", location.pathname + location.search);
}
let online = navigator.onLine,
  paired = false,
  activeUpload = false,
  reconnecting = false;
let eventSource: EventSource | undefined;
let queue = loadQueue();
const liveFiles = new Map<string, File>(),
  controllers = new Map<string, AbortController>();
const retryOnReconnect = new Set<string>();
const root = document.querySelector<HTMLDivElement>("#app")!;
const secure = window.isSecureContext && location.protocol === "https:";
let drafts: ShareDraft[] = [];
let expiryTimer: number | undefined;
const busyDrafts = new Set<string>();
let shareNotice:
  | "shareReceived"
  | "shareSent"
  | "shareError"
  | "shareActionError"
  | "shareUnavailable"
  | undefined;
let copyNotice: "copied" | "copyFailed" | undefined;
if (secure && new URLSearchParams(location.search).get("share") === "failed") {
  shareNotice = "shareError";
  const url = new URL(location.href);
  url.searchParams.delete("share");
  history.replaceState(null, "", url);
}

function escapeHtml(value: string): string {
  return value.replace(
    /[&<>"']/g,
    (ch) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        ch
      ]!,
  );
}
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let i = -1;
  do {
    bytes /= 1024;
    i++;
  } while (bytes >= 1024 && i < units.length - 1);
  return `${bytes.toFixed(1)} ${units[i]}`;
}
function transferPercent(item: UploadRecord): number {
  return item.status === "done"
    ? 100
    : Math.min(
        100,
        Math.round(((item.bytesDone ?? 0) / Math.max(1, item.size)) * 100),
      );
}
function updateTransfer(item: UploadRecord): void {
  const row = Array.from(root.querySelectorAll<HTMLElement>(".transfer")).find(
    (node) => node.dataset.key === item.key,
  );
  if (!row) return;
  const progress = row.querySelector<HTMLProgressElement>("progress");
  const label = row.querySelector<HTMLElement>("small");
  const percent = transferPercent(item);
  if (progress) {
    progress.value = percent;
    progress.setAttribute("aria-label", `${item.name} ${percent}%`);
  }
  if (label) {
    const hashing =
      item.status === "uploading" && (item.bytesHashed ?? 0) < item.size;
    const detail =
      item.status === "done"
        ? copy.done
        : item.status === "error"
          ? item.error || copy.failed
          : item.status === "uploading"
            ? `${hashing ? copy.hashing : copy.sent} ${formatSize(hashing ? (item.bytesHashed ?? 0) : (item.bytesDone ?? 0))} / ${formatSize(item.size)}`
            : copy.waiting;
    label.textContent = `${formatSize(item.size)} · ${detail}`;
  }
}

function renderDrafts(): string {
  return `${shareNotice ? `<p class="share-notice" role="status">${copy[shareNotice]}</p>` : ""}${drafts
    .map(({ id, text, files }) => {
      const busy = busyDrafts.has(id);
      return `<section class="card share-card" data-draft="${escapeHtml(id)}">
      <h2>${copy.shareDraft}</h2>
      ${text ? `<p class="share-preview" dir="auto">${escapeHtml(text)}</p>` : ""}
      ${files.length ? `<ul class="share-files">${files.map((file) => `<li><bdi>${escapeHtml(file.name)}</bdi> <small>${formatSize(file.size)}</small></li>`).join("")}</ul>` : ""}
      ${paired ? "" : `<p class="muted">${copy.sharePair}</p>`}
      <div class="share-actions">
        <button type="button" class="primary" data-draft-action="send" ${paired && online && !busy ? "" : "disabled"}>${copy.shareSend}</button>
        <button type="button" class="secondary" data-draft-action="discard" ${busy ? "disabled" : ""}>${copy.shareDiscard}</button>
      </div>
    </section>`;
    })
    .join("")}`;
}

function render(): void {
  document.documentElement.lang = language;
  document.documentElement.dir = language === "ar" ? "rtl" : "ltr";
  const oldMessages = root.querySelector("#messages")?.innerHTML,
    oldOutbox = root.querySelector("#outbox")?.innerHTML;
  const draft =
    root.querySelector<HTMLTextAreaElement>('textarea[name="text"]')?.value ??
    "";
  const pinValue = root.querySelector<HTMLInputElement>("#pin")?.value ?? "";
  const active =
    document.activeElement instanceof HTMLElement
      ? document.activeElement.id
      : "";
  const textSelection =
    document.activeElement instanceof HTMLTextAreaElement
      ? ([
          document.activeElement.selectionStart,
          document.activeElement.selectionEnd,
        ] as const)
      : undefined;
  root.innerHTML = `<header><div class="brand"><span class="mark" aria-hidden="true">↔</span><div><h1>${copy.title}</h1></div></div><button class="lang" id="lang" type="button">${language === "en" ? "العربية" : "English"}</button></header><main>${renderDrafts()}${
    !paired
      ? `<section class="card pairing"><div class="eyebrow">${copy.pair}</div><p class="muted">${copy.instruction}</p><form id="pairForm"><label for="pin">${copy.pin}</label><div class="join"><input id="pin" name="pin" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" maxlength="6" placeholder="••••••" required><button class="primary" type="submit">${copy.connect}</button></div><div id="pairError" class="error" role="alert"></div></form></section>`
      : `<div class="status ${online ? "" : "offline"}" role="status"><i></i>${online ? copy.online : copy.offline}<span>${copy.paired}</span></div>
<section class="card"><div class="section-head"><div><div class="eyebrow">${copy.files}</div><h2>${copy.queue}</h2></div></div><div class="picker"><label class="pick primary">＋ ${copy.pick}<input id="files" type="file" multiple></label><label class="pick secondary">▧ ${copy.folder}<input id="folder" type="file" webkitdirectory directory multiple></label></div><div class="queue">${queue.length ? queue.map((item) => `<article class="transfer" data-key="${escapeHtml(item.key)}"><div class="fileicon" aria-hidden="true">${/\.(jpg|png|heic|webp)$/i.test(item.name) ? "▧" : "↥"}</div><div class="fileinfo"><strong>${escapeHtml(item.name)}</strong><small>${formatSize(item.size)} · ${item.status === "done" ? copy.done : item.status === "uploading" ? `${(item.bytesHashed ?? 0) < item.size ? copy.hashing : copy.sent} ${formatSize((item.bytesHashed ?? 0) < item.size ? (item.bytesHashed ?? 0) : (item.bytesDone ?? 0))} / ${formatSize(item.size)}` : item.status === "error" ? escapeHtml(item.error || copy.failed) : copy.waiting}</small><progress max="100" value="${transferPercent(item)}" aria-label="${escapeHtml(item.name)} ${transferPercent(item)}%"></progress></div>${item.status !== "done" && (item.status === "error" || !liveFiles.has(item.key)) ? `<button class="action resume" data-key="${escapeHtml(item.key)}" type="button">${item.cancelPending ? copy.retryCleanup : copy.resume}</button>` : ""}<button class="iconbtn remove" data-key="${escapeHtml(item.key)}" type="button" aria-label="${copy.remove}">×</button></article>`).join("") : `<p class="empty">${copy.empty}</p>`}</div></section>
<section class="card"><div class="eyebrow">${copy.received}</div><div id="outbox" class="list"><p class="empty">${copy.empty}</p></div></section><section class="card"><div class="eyebrow">${copy.text}</div><div id="messages" class="messages"><p class="empty">${copy.empty}</p></div><p id="copy-status" class="muted copy-status" role="status">${copyNotice ? copy[copyNotice] : ""}</p><form id="textForm" class="textform"><textarea id="share-text" name="text" rows="2" maxlength="10000" placeholder="${copy.write}" required></textarea><button class="primary" type="submit">${copy.send}</button></form></section>`
  }</main>`;
  if (oldMessages && root.querySelector("#messages")) {
    root.querySelector("#messages")!.innerHTML = oldMessages;
    root.querySelectorAll(".copy-text").forEach((button) => {
      button.textContent = copy.copyText;
    });
  }
  if (oldOutbox && root.querySelector("#outbox"))
    root.querySelector("#outbox")!.innerHTML = oldOutbox;
  const textarea = root.querySelector<HTMLTextAreaElement>(
    'textarea[name="text"]',
  );
  if (textarea) textarea.value = draft;
  if (textarea && active === "share-text") {
    textarea.focus();
    if (textSelection) textarea.setSelectionRange(...textSelection);
  }
  const pin = root.querySelector<HTMLInputElement>("#pin");
  if (pin) {
    pin.value = pinValue;
    if (active === "pin") pin.focus();
  }
  root.querySelector("#lang")?.addEventListener("click", () => {
    language = language === "en" ? "ar" : "en";
    copy = translations[language];
    localStorage.setItem("duet.lang", language);
    render();
    if (active) root.querySelector<HTMLElement>(`#${active}`)?.focus();
  });
  root
    .querySelector<HTMLFormElement>("#pairForm")
    ?.addEventListener("submit", (e) => void pairWithPin(e));
  root
    .querySelector<HTMLInputElement>("#files")
    ?.addEventListener("change", (e) =>
      addFiles((e.currentTarget as HTMLInputElement).files),
    );
  root
    .querySelector<HTMLInputElement>("#folder")
    ?.addEventListener("change", (e) =>
      addFiles((e.currentTarget as HTMLInputElement).files, true),
    );
  root
    .querySelectorAll<HTMLButtonElement>(".resume")
    .forEach((b) =>
      b.addEventListener("click", () => void resumeOne(b.dataset.key!)),
    );
  root
    .querySelectorAll<HTMLButtonElement>(".remove")
    .forEach((b) =>
      b.addEventListener("click", () => removeOne(b.dataset.key!)),
    );
  root
    .querySelector<HTMLFormElement>("#textForm")
    ?.addEventListener("submit", (e) => void sendText(e));
}

async function refreshDrafts(): Promise<void> {
  try {
    const pending = await listDrafts();
    const changed =
      JSON.stringify(pending.map(({ id, text }) => [id, text])) !==
      JSON.stringify(drafts.map(({ id, text }) => [id, text]));
    drafts = pending;
    window.clearTimeout(expiryTimer);
    if (drafts.length) {
      const deadline = Math.min(...drafts.map((draft) => draft.expiresAt));
      expiryTimer = window.setTimeout(
        () => void refreshDrafts(),
        Math.max(0, deadline - Date.now()),
      );
    }
    if (changed) render();
  } catch {
    drafts = [];
    shareNotice = "shareUnavailable";
    render();
  }
}

async function updateDraft(id: string, send: boolean): Promise<void> {
  if (busyDrafts.has(id) || (send && (!paired || !online))) return;
  busyDrafts.add(id);
  shareNotice = undefined;
  render();
  try {
    await navigator.locks.request("duet.share", async () => {
      const draft = (await listDrafts()).find((item) => item.id === id);
      if (!draft) return;
      if (send) {
        if (!paired || !online) throw new Error("Pairing required");
        if (draft.text) {
          await postText(draft.text);
          await clearDraftText(id);
          void refreshText();
        }
        if (draft.files.length) {
          enqueueFiles(draft.files, false, id);
          shareNotice = "shareReceived";
        } else shareNotice = "shareSent";
      }
      await deleteDraft(id);
    });
  } catch (error) {
    shareNotice = "shareActionError";
    if (error instanceof HttpError && error.status === 401)
      await checkSession();
  } finally {
    busyDrafts.delete(id);
    await refreshDrafts();
    render();
    void pump();
  }
}

async function copyMessage(button: HTMLButtonElement): Promise<void> {
  const text = button.closest(".message")?.querySelector("p")?.textContent;
  if (!secure || text == null) return;
  try {
    await navigator.clipboard.writeText(text);
    copyNotice = "copied";
  } catch {
    copyNotice = "copyFailed";
  }
  const status = root.querySelector("#copy-status");
  if (status) status.textContent = copy[copyNotice];
}

root.addEventListener("click", (event) => {
  if (!(event.target instanceof HTMLButtonElement)) return;
  if (event.target.matches(".copy-text")) void copyMessage(event.target);
  const action = event.target.dataset.draftAction;
  const id = event.target.closest<HTMLElement>("[data-draft]")?.dataset.draft;
  if (id && action) void updateDraft(id, action === "send");
});
function persist(): void {
  saveQueue(queue);
}
async function pairRequest(
  body: { token: string } | { pin: string },
): Promise<void> {
  const error = root.querySelector<HTMLElement>("#pairError");
  try {
    const response = await fetch("/api/pair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      if (error)
        error.textContent =
          response.status === 401 ? copy.badPin : copy.connection;
      return;
    }
    await checkSession();
  } catch {
    if (error) error.textContent = copy.connection;
  }
}
async function pairWithPin(event: SubmitEvent): Promise<void> {
  event.preventDefault();
  await pairRequest({
    pin: String(
      new FormData(event.currentTarget as HTMLFormElement).get("pin") || "",
    ),
  });
}
function makeKey(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
function addFiles(files: FileList | null, folder = false): void {
  if (!files) return;
  enqueueFiles(Array.from(files), folder);
  render();
  void pump();
}
function enqueueFiles(files: File[], folder: boolean, draftId?: string): void {
  const updated = [...queue];
  const available = new Map<string, File>();
  for (const [index, file] of files.entries()) {
    const relpath = (file as File & { webkitRelativePath?: string })
      .webkitRelativePath;
    const key = draftId ? `${draftId}-${index}` : makeKey();
    const saved = updated.find((item) => item.key === key);
    if (saved) {
      if (saved.status !== "done") available.set(key, file);
      continue;
    }
    available.set(key, file);
    updated.push({
      key,
      name: file.name,
      size: file.size,
      ...(folder && relpath ? { relpath } : {}),
      status: "queued",
      bytesDone: 0,
    });
  }
  saveQueue(updated);
  queue = updated;
  for (const [key, file] of available) liveFiles.set(key, file);
}
async function reselect(record: UploadRecord): Promise<File | null> {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.hidden = true;
    document.body.append(input);
    let done = false;
    const finish = (file: File | null) => {
      if (done) return;
      done = true;
      input.remove();
      resolve(file);
    };
    input.addEventListener("change", () => {
      const file = input.files?.[0];
      if (!file) {
        finish(null);
        return;
      }
      if (file.name !== record.name || file.size !== record.size) {
        alert(copy.mismatch);
        finish(null);
        return;
      }
      finish(file);
    });
    input.addEventListener("cancel", () => finish(null));
    input.click();
  });
}
async function resumeOne(key: string): Promise<void> {
  const record = queue.find((item) => item.key === key);
  if (!record || activeUpload || !online || !paired) return;
  if (record.cancelPending) {
    await discardUpload(record);
    return;
  }
  const file = liveFiles.get(key) ?? (await reselect(record));
  if (!file) return;
  liveFiles.set(key, file);
  record.status = "queued";
  record.error = undefined;
  persist();
  render();
  await pump(key);
}
async function discardUpload(record: UploadRecord): Promise<void> {
  if (!record.uploadId) return;
  record.cancelPending = true;
  controllers.get(record.key)?.abort();
  persist();
  try {
    const response = await fetch(
      `/api/uploads/${encodeURIComponent(record.uploadId)}`,
      { method: "DELETE" },
    );
    if (response.status === 401) await checkSession();
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    queue = queue.filter((item) => item.key !== record.key);
    liveFiles.delete(record.key);
    retryOnReconnect.delete(record.key);
  } catch {
    record.status = "error";
    record.error = copy.removeFailed;
  }
  persist();
  render();
}
function isTransientFailure(error: unknown): boolean {
  if (!(error instanceof HttpError)) return true;
  return (
    error.status === 408 ||
    error.status === 425 ||
    error.status === 429 ||
    error.status >= 500
  );
}
function resumeInterrupted(): void {
  if (!paired || !online || retryOnReconnect.size === 0) return;
  for (const key of retryOnReconnect) {
    const record = queue.find((item) => item.key === key);
    if (record && liveFiles.has(key) && !record.cancelPending) {
      record.status = "queued";
      record.error = undefined;
      retryOnReconnect.delete(key);
    }
  }
  retryOnReconnect.clear();
  persist();
  render();
  void pump();
}
async function pump(preferredKey?: string): Promise<void> {
  if (activeUpload || !online || !paired) return;
  const next = preferredKey
    ? queue.find((item) => item.key === preferredKey)
    : queue.find((item) => item.status === "queued" && liveFiles.has(item.key));
  if (!next) return;
  const file = liveFiles.get(next.key);
  if (!file) return;
  activeUpload = true;
  const controller = new AbortController();
  controllers.set(next.key, controller);
  next.status = "uploading";
  persist();
  render();
  try {
    await runUpload(
      file,
      { ...next },
      controller.signal,
      (updated) => {
        const metadataChanged =
          updated.sha256 !== next.sha256 ||
          updated.uploadId !== next.uploadId ||
          updated.chunkSize !== next.chunkSize ||
          updated.bytesDone !== next.bytesDone ||
          updated.status !== next.status;
        Object.assign(next, updated);
        if (metadataChanged) persist();
        updateTransfer(next);
      },
      async (duplicate) =>
        confirm(
          `${copy.duplicate}\n${duplicate.relpath}\n${formatSize(duplicate.size)}`,
        ),
    );
  } catch (error) {
    if (!controller.signal.aborted) {
      next.status = "error";
      next.error =
        language === "ar"
          ? copy.failed
          : error instanceof Error
            ? error.message
            : copy.failed;
      if (isTransientFailure(error)) retryOnReconnect.add(next.key);
      if (error instanceof HttpError && error.status === 401) {
        paired = false;
        eventSource?.close();
        eventSource = undefined;
      }
    }
  } finally {
    controllers.delete(next.key);
    if (queue.find((item) => item.key === next.key)?.status === "done")
      liveFiles.delete(next.key);
    persist();
    activeUpload = false;
    render();
    if (online) void pump();
  }
}
function removeOne(key: string): void {
  const item = queue.find((record) => record.key === key);
  if (item?.uploadId && item.status !== "done") {
    item.cancelPending = true;
    item.error = undefined;
    render();
    void discardUpload(item);
    return;
  }
  controllers.get(key)?.abort();
  liveFiles.delete(key);
  retryOnReconnect.delete(key);
  queue = queue.filter((item) => item.key !== key);
  persist();
  render();
}
async function sendText(event: SubmitEvent): Promise<void> {
  event.preventDefault();
  const form = event.currentTarget as HTMLFormElement;
  const textarea = form.elements.namedItem("text") as HTMLTextAreaElement;
  const text = textarea.value.trim();
  if (!text) return;
  try {
    await postText(text);
    textarea.value = "";
    await refreshText();
  } catch (error) {
    if (error instanceof HttpError && error.status === 401) {
      await checkSession();
      return;
    }
    alert(copy.connection);
  }
}
async function postText(text: string): Promise<void> {
  const response = await fetch("/api/text", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!response.ok) throw new HttpError(response.status, copy.connection);
}
async function refreshText(): Promise<void> {
  try {
    const response = await fetch("/api/text");
    if (response.status === 401) {
      await checkSession();
      return;
    }
    if (!response.ok) return;
    const data = (await response.json()) as {
      messages: Array<{
        id: string;
        text: string;
        source: string;
        created_at: string;
      }>;
    };
    const el = root.querySelector<HTMLElement>("#messages");
    if (el)
      el.innerHTML = data.messages.length
        ? data.messages
            .map(
              (m) =>
                `<article class="message"><p dir="auto">${escapeHtml(m.text)}</p><div class="message-actions"><small>${escapeHtml(m.source)} · ${new Date(m.created_at).toLocaleTimeString(language)}</small>${secure ? `<button type="button" class="copy-text">${copy.copyText}</button>` : ""}</div></article>`,
            )
            .join("")
        : `<p class="empty">${copy.empty}</p>`;
  } catch {}
}
async function refreshOutbox(): Promise<void> {
  try {
    const response = await fetch("/api/outbox");
    if (response.status === 401) {
      await checkSession();
      return;
    }
    const el = root.querySelector<HTMLElement>("#outbox");
    if (!el) return;
    if (response.status === 404) {
      el.innerHTML = `<p class="empty">${copy.empty}</p>`;
      return;
    }
    if (!response.ok) return;
    const data = (await response.json()) as {
      files: Array<{
        id: string;
        name: string;
        size: number;
        is_folder: boolean;
      }>;
    };
    el.innerHTML = data.files.length
      ? data.files
          .map(
            (f) =>
              `<a class="outitem" href="/api/files/${encodeURIComponent(f.id)}" download><span>${f.is_folder ? "▧" : "↓"}</span><span>${escapeHtml(f.name)}<small>${f.is_folder ? copy.folderLabel : formatSize(f.size)}</small></span><b aria-hidden="true">↓</b></a>`,
          )
          .join("")
      : `<p class="empty">${copy.empty}</p>`;
  } catch {}
}
async function refresh(): Promise<void> {
  await Promise.all([refreshText(), refreshOutbox()]);
}
function connectEvents(): void {
  if (eventSource || !paired || typeof EventSource === "undefined") return;
  eventSource = new EventSource("/api/events");
  for (const name of ["text", "outbox", "upload"])
    eventSource.addEventListener(name, () => void refresh());
  eventSource.onopen = () => resumeInterrupted();
  eventSource.onerror = () => {
    eventSource?.close();
    eventSource = undefined;
    if (reconnecting) return;
    reconnecting = true;
    void checkSession();
    setTimeout(() => {
      reconnecting = false;
      if (paired) connectEvents();
    }, 2500);
  };
}
async function checkSession(): Promise<void> {
  try {
    const response = await fetch("/api/session");
    const session = (await response.json()) as {
      authenticated?: boolean;
      paired?: boolean;
    };
    paired =
      response.ok &&
      session.authenticated !== false &&
      session.paired !== false;
  } catch {
    paired = false;
  }
  if (!paired) {
    eventSource?.close();
    eventSource = undefined;
  }
  render();
  if (paired) {
    await refresh();
    connectEvents();
    resumeInterrupted();
  }
}
window.addEventListener("online", () => {
  online = true;
  render();
  void checkSession().then(resumeInterrupted);
});
window.addEventListener("offline", () => {
  online = false;
  for (const [key, controller] of controllers) {
    const record = queue.find((item) => item.key === key);
    if (record && !record.cancelPending) {
      retryOnReconnect.add(key);
      record.status = "error";
      record.error = copy.connection;
      controller.abort();
    }
  }
  render();
});
if (
  secure &&
  "serviceWorker" in navigator &&
  "locks" in navigator &&
  "indexedDB" in window
) {
  const manifest = document.createElement("link");
  manifest.rel = "manifest";
  manifest.href = "/manifest.webmanifest";
  document.head.append(manifest);
  void navigator.serviceWorker
    .register("/sw.js", { type: "module" })
    .catch(() => {
      shareNotice = "shareUnavailable";
      render();
    });
  void refreshDrafts();
  window.addEventListener("focus", () => void refreshDrafts());
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) void refreshDrafts();
  });
}
render();
if (qrToken) {
  const token = qrToken;
  qrToken = "";
  void pairRequest({ token });
} else void checkSession();

# Duet: your phone and PC working together (plan)

Working name **Duet** (rename freely). Put this file in the repo as `docs/PLAN.md`.

Duet grows in two stages:
- **Stage 1 (M0–M8):** file and text transfer between any phone and the PC, through the phone's browser, with nothing to install on the phone.
- **Stage 2 (M9–M14):** phone-companion features on Android:
  - clipboard sync;
  - notifications on the PC (with replies);
  - SMS;
  - battery status;
  - media controls;
  - "ring my phone";
  - screen mirroring.
  
  These use the **existing open-source KDE Connect Android app** on the phone. Duet's PC side speaks its protocol, so no phone app has to be written.

## 1. What it is

A PC app (Windows and Linux) that lets any phone send files to the PC and receive files from it over the **local network**. No cloud, no account, no internet needed, and **nothing to install on the phone**: the phone uses its normal browser.

- The PC app shows a **QR code**. Scanning it on the phone opens the Duet page, served by the PC itself.
- **Phone → PC:** pick files or folders (photos, videos, documents), and they upload straight to a "Received" folder on the PC.
- **PC → phone:** drag files onto the PC app (or use a "Send to phone" right-click option later), and they appear on the phone page to download.
- **Also:** send a piece of text or a link either way, as a shared clipboard.
- **Works over:**
  - the home Wi-Fi;
  - **the PC's own hotspot** (Windows Mobile Hotspot, or a Linux hotspot) when there's no router or no internet: the phone connects straight to the PC.

Stage 1 is similar in spirit to LocalSend or Snapdrop. The difference: a browser-only phone side, a hotspot-first setup, and resumable transfers of very large files. Stage 2 brings Phone Link-style features to Windows and Linux through the KDE Connect Android app.

## 2. Core decisions (don't change without asking Ahmed)

1. **No phone app in v1.** The phone side is a web app served by the PC and works in any modern mobile browser (Chrome on Android, Safari on iPhone).
2. **Local only.** The server listens only on private network addresses (192.168.x, 10.x, 172.16–31.x, the hotspot range). No external calls, no telemetry.
3. **Pairing is required.** Every session needs the one-time token from the QR code, or a 6-digit PIN shown on the PC. Sessions expire. The PC user can see and revoke connected devices.
4. **Received files never overwrite.** Name clashes get `name (1).ext`. Files are written to a temporary `.part` file and only moved into place after the size and SHA-256 checks pass.
5. **Resumable.** Uploads are sent in chunks and resume after a dropped connection or a locked phone. Downloads support HTTP Range requests.
6. **The server is off until the user starts it,** and it stops after a period with no activity (configurable).

## 3. Stack

| Part | Choice | Why |
|---|---|---|
| PC core and server | Python 3.11+, `aiohttp` (or Starlette + uvicorn) | async I/O for many large streams; one language with the GUI |
| PC GUI | PySide6: main window plus a tray icon | Ahmed already knows it; works on Windows and Linux |
| Phone web app | TypeScript, Vite, no heavy framework (or Preact) | small bundle, works on mobile browsers, built into static files that the Python package serves |
| QR code | `qrcode` (Python) | shown in the GUI and in the terminal |
| Tests | pytest + an async test client; Vitest for the web app logic; optional Playwright end-to-end in its own CI job | the main suites run headless with no real phone |
| Packaging | `pyproject.toml`, `src/` layout; PyInstaller later | — |

**Repo layout:**
- `src/duet/core/`: sessions, pairing, the transfer engine, storage, the history DB (SQLite);
- `src/duet/server/`: HTTP routes, the static web app;
- `src/duet/gui/`;
- `src/duet/cli/`;
- `web/`: the TypeScript source;
- `tests/`;
- `docs/`.

The core has **no GUI imports**, so the CLI (`duet serve`, `duet send FILE`, `duet devices`, `duet history`) runs without Qt.

## 4. Protocol (v1)

- `POST /api/pair` with a token or PIN → returns a session cookie (HttpOnly, SameSite=Strict). Rate-limited: 5 wrong PINs → a 60 s lockout.
- **Upload:**
  - `POST /api/uploads {name, size, sha256?, relpath?}` → returns `upload_id` and `chunk_size` (default 8 MiB);
  - `PUT /api/uploads/{id}/chunks/{n}` (each chunk with its own SHA-256 header);
  - `GET /api/uploads/{id}` lists the chunks received, so a resumed upload only sends what's missing;
  - `POST /api/uploads/{id}/complete` verifies, then moves the file into place.
- **Download:**
  - `GET /api/outbox` lists what the PC is offering;
  - `GET /api/files/{id}` supports Range requests;
  - folders are offered as a zip created on the fly.
- **Text:** `POST /api/text`, and a list endpoint for messages.
- **Live updates:** Server-Sent Events, `GET /api/events`, for progress, new outbox items and new texts.
- **Path safety:**
  - received names are cleaned (no `..`, absolute paths, Windows-reserved names or control characters);
  - long names are shortened;
  - `relpath` from folder uploads is kept, but always stays inside the Received folder.
- **Limits:** max file size (default none), free-disk check before accepting, and max parallel uploads per device (default 3).

## 5. Security notes

- v1 runs over plain HTTP on the local network, so traffic isn't encrypted. That's fine at home and over the PC's own hotspot; on **public or shared Wi-Fi** the PC app shows a warning.
- HTTPS (a self-signed certificate, with its fingerprint in the QR code) is a later milestone. Browsers show a warning for self-signed certificates, so that phase includes a guided "trust this PC" flow. HTTPS also unlocks "Share to Duet" from the Android share sheet (PWA share target) and in-browser encryption, which both need a secure connection.
- **Windows firewall:** on first start, explain the prompt and allow private networks only.

## 6. Milestones

Each ends in a runnable state with tests. "Done when" commands run on Linux.

| # | Milestone | Main work | Done when |
|---|---|---|---|
| M0 | Skeleton | package layout, config, logging, CLI entry point, CI (lint + tests on Windows and Linux), web build wired into the package | `pip install -e . && duet --version && pytest -q` passes; CI green |
| M1 | Pairing and server | sessions, token/PIN pairing, rate limit, binding only to local addresses, SSE, `duet serve` printing a terminal QR code | tests for pairing, expiry, lockout, rejecting public addresses |
| M2 | Phone → PC uploads | chunked resumable uploads, `.part` + SHA-256 check + atomic move, safe names, name clashes, disk-space check, folder uploads | an end-to-end test sends a generated 200 MB file in chunks, drops halfway, resumes, and the result is byte-identical; malicious names are cleaned |
| M3 | Phone web app v1 | pair screen, file and folder picker, queue with progress, retry and resume, received list, text sharing; mobile layout; English and Arabic (right-to-left) | Vitest for the chunker, resume and queue logic; the static bundle is served by `duet serve` |
| M4 | PC → phone | outbox, Range downloads, zip of folders created on the fly, `duet send FILE` | tests for Range resume and zip contents |
| M5 | Desktop GUI | PySide6 window and tray: start/stop, QR code, connected devices (revoke), live transfers, history, open the Received folder, drag-and-drop to send, settings | GUI tests on `QT_QPA_PLATFORM=offscreen` |
| M6 | Hotspot and network UX | detect hotspot or Wi-Fi addresses, pick the right IP for the QR code, guide for the Windows Mobile Hotspot and a Linux hotspot, firewall explainer, public-network warning, auto-stop after idle | tests for address selection and the idle stop |
| M7 | Polish and packaging | history search, notifications, Windows installer (PyInstaller + installer), Linux AppImage or .deb, README with screenshots | installers build in CI |
| M8 | HTTPS + share target | self-signed cert, fingerprint in the QR code, "trust this PC" guide, PWA install and Android "Share to Duet", tap-to-copy into the clipboard (needs HTTPS), optional end-to-end encryption | tests for certificate creation and fingerprint checks; manual phone checklist |
| **Stage 2** | | | |
| M9 | Companion protocol core | an independent implementation of the KDE Connect protocol on the PC: device discovery (UDP broadcast on port 1716 + TCP), TLS with per-device certificates, pairing with a confirmation on both sides, the packet framework, connection keep-alive and reconnects | tests with a **simulated phone peer** in the test suite: discovery, pairing, rejecting unpaired devices, reconnects |
| M10 | Clipboard and sharing | clipboard sync both ways (the PC side watches its clipboard; on the phone, Android 10+ only allows sending from the share sheet, a quick-settings tile or a notification button, so document this clearly); files and links through the companion app go into the stage 1 Received folder and history | simulated-peer tests; manual checklist on a real Android phone |
| M11 | Notifications | phone notifications shown on the PC (Windows toast / Linux desktop notifications), per-app filters, reply to messaging notifications, dismissing syncs both ways, privacy option to hide contents | tests on the simulated peer; GUI tests offscreen |
| M12 | Phone status and control | battery level and charging alerts, "ring my phone", media controls (play/pause/next for what plays on the phone, and the phone controlling PC media), find-my-PC | simulated-peer tests |
| M13 | SMS | conversation list, read and send SMS from the PC, new-message notifications | simulated-peer tests; manual checklist |
| M14 | Screen mirroring | launch and manage **scrcpy** (USB or Wi-Fi ADB) from Duet: detection, a one-click guide for enabling USB debugging, start/stop, window per device. Duet does not reimplement mirroring | tests for the scrcpy wrapper with a fake binary |

## 7. Tests

- **Generated data only:** random bytes, generated folders, names in Arabic and other Unicode, and hostile names. No real personal files.
- **Server:** an async test client covering pairing, every route, auth on every route, chunk corruption (a wrong chunk hash is rejected), concurrent uploads, a dropped connection, a full disk (simulated), and slow clients.
- **Web app:** Vitest with jsdom for the chunker, retry/backoff, resume state, queue ordering and text sharing.
- **Optional end-to-end:** Playwright with a mobile viewport against `duet serve`, in a separate CI job. The main suite must not need a browser download.
- **Speed:** the full main suite runs in under 3 minutes on Linux.

## 8. Edge cases to handle

- Files over 4 GB.
- A phone locking or switching apps mid-upload: resume.
- Two phones sending at the same time.
- The same file sent twice: offer to skip if the SHA-256 matches.
- iOS Safari limits: photo picker formats (HEIC vs JPEG conversion), the download location.
- Android browsers killing background tabs.
- A PC with several network adapters (VPN, Docker, WSL).
- An address change mid-session.
- Read-only or missing Received folder.
- Windows long paths and reserved names.
- Antivirus locking a `.part` file.

## 9. Stage 2 rules (KDE Connect compatibility)

- **Write the code from the protocol documentation and observed behavior only.** KDE Connect is GPL-licensed, so **never copy its code** into Duet; Duet stays MIT. Record the protocol references used in `docs/protocol.md`.
- **The protocol layer is isolated** (`src/duet/companion/`) behind an interface, so a native Duet phone app could replace it later.
- **Every feature can be switched off per device** (clipboard, notifications, SMS), and **SMS and notification contents are never logged or stored on disk** beyond what's shown in the UI (no history unless the user turns it on).
- **iPhone:** Apple doesn't allow these background features for apps like this, so on iPhone only stage 1 (files and text through the browser) works. Say so in the UI and the README.
- **Windows firewall:** stage 2 also needs UDP and TCP port 1716 (and its range) on private networks only.

## 10. Out of scope

- Writing a native phone app. Stage 2 uses the KDE Connect app; a native Duet app is a possible later project.
- Wi-Fi Direct.
- Internet or relay connections.
- Accounts and cloud sync.
- Deleting files on the phone.
- Phone calls through the PC (audio routing).

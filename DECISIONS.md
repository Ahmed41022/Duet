# Duet decisions

| Date | Decision | Options considered | Reason | Task |
| --- | --- | --- | --- | --- |
| 2026-11-25 | Host development in the public Ahmed41022/Duet repository. | New public repository, new private repository, existing repository | Ahmed selected the new public repository for pull requests and Windows/Linux CI. | M0 |
| 2026-11-25 | Build the Vite client into the Python server package before distribution. | Include compiled assets in the wheel, or require Node.js on each PC | The installed PC app can serve the phone UI offline. Only developers and CI need Node.js. | M0 |
| 2026-11-25 | Use a TOML settings file with explicit loading and in-memory defaults. | TOML, JSON, or environment variables alone | Python 3.11 reads TOML with its standard library. Loading defaults leaves the filesystem untouched. | M0 |
| 2026-11-25 | Use aiohttp with an explicit RFC 1918 bind address and check the origin of browser API requests. | aiohttp or an ASGI stack | One server handles streaming, pairing and live events, with no external services. | M1 |
| 2026-11-25 | Rotate a single-use QR/PIN after pairing or ten minutes. Sessions last eight hours. | Independent or shared offers, fixed or sliding expiry | Either credential consumes the offer. A fixed session lifetime and PC-side revocation bound access. | M1 |
| 2026-11-25 | Track chunks per paired session. Support idempotent creation, completion and cancellation. | In-memory or persistent manifests | A disconnected phone can recover its upload during the active server session, including when it lost the response that assigned the upload ID. | M2 |
| 2026-11-25 | Publish verified staging files using Windows non-replacing rename or POSIX hard links. | Replace, non-replacing publication, or a separate copy | Concurrent completions keep distinct filenames and partial content stays hidden from the final path. Linux Received volumes must support hard links. | M2 |

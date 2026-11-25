# Duet decisions

| Date | Decision | Options considered | Reason | Task |
| --- | --- | --- | --- | --- |
| 2026-11-25 | Host development in the public Ahmed41022/Duet repository. | New public repository, new private repository, existing repository | Ahmed selected the new public repository for pull requests and Windows/Linux CI. | M0 |
| 2026-11-25 | Build the Vite client into the Python server package before distribution. | Include compiled assets in the wheel, or require Node.js on each PC | The installed PC app can serve the phone UI offline. Only developers and CI need Node.js. | M0 |
| 2026-11-25 | Use a TOML settings file with explicit loading and in-memory defaults. | TOML, JSON, or environment variables alone | Python 3.11 reads TOML with its standard library. Loading defaults leaves the filesystem untouched. | M0 |

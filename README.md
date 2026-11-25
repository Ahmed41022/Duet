# Duet

Duet is a local file and text sharing app for Windows and Linux. The phone uses its browser. See the [project plan](docs/PLAN.md) for the milestones.

## Development setup

Install Python 3.11 or later. To build or preview the phone web app, also install Node.js 22.12 or later with npm.

Create a virtual environment with `python -m venv .venv`. Activate it with `.venv\Scripts\Activate.ps1` in Windows PowerShell or `source .venv/bin/activate` on Linux. Then run:

```sh
python -m pip install -e ".[dev]"
duet --version
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

The Python suite runs without Qt, a browser, or a phone.

## Phone web app and Python package

From the repository root, run:

```sh
npm ci --prefix web
npm run build --prefix web
python -m build
```

Vite writes the static bundle to `src/duet/server/static/`. Build the web app before creating distribution archives so the bundle is included. The wheel and source distribution appear in `dist/`.

To preview the starter page while editing it, run `npm run dev --prefix web` and open the local address Vite prints.

## Configuration

`duet.core.config.load_config()` provides defaults without creating files or directories. Python callers can pass a TOML file to override them. Relative received directories resolve from that file's directory.

```toml
received_directory = "Received"
idle_timeout_seconds = 900
log_level = "INFO"
```

The default received directory is `Duet/Received` under the user's home directory.

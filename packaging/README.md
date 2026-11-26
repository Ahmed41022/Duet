# Desktop packages

The package build freezes Duet into a self-contained PyInstaller one-folder app. It includes the Qt runtime and the built phone web page under `duet/server/static`.

## Build

From the repository root, install Python 3.11+, Node.js 22+, and the desktop extra and PyInstaller:

```sh
python -m pip install -e ".[desktop]" "PyInstaller>=6.12,<7"
npm ci --prefix web
npm run build --prefix web
python -m PyInstaller --clean --noconfirm packaging/desktop.spec
```

On Windows, install Inno Setup 6 and run `ISCC.exe packaging/duet.iss`. This creates `dist/installer/Duet-Setup-<version>-win64.exe`, which installs for the current user under `%LocalAppData%\Programs\Duet` by default. The installer offers a desktop shortcut and Start menu entry.

On Debian or Ubuntu amd64, install `dpkg-dev` and the Qt runtime dependencies listed in `build_deb.sh`, then run `packaging/build_deb.sh`. The resulting `dist/duet_<version>_amd64.deb` installs the app in `/opt/duet` with a `duet-desktop` launcher and application menu entry. CI builds on Ubuntu 24.04; the package requires glibc 2.39 or newer, so older Debian/Ubuntu releases are not supported by that artifact.

Both packages include a license directory with the Duet license, Qt/PySide licensing notice, and PyInstaller bootloader license. The packages use the same desktop executable for normal use and smoke checking. Run it with `--smoke-test` and `QT_QPA_PLATFORM=offscreen` in a headless environment to verify the packaged Qt window opens and closes and that the embedded HTML, CSS, and JavaScript are readable. The smoke mode uses a temporary data directory. CI installs each generated package and runs this check before uploading the installer/deb as a workflow artifact.

## Run from a build folder

Launch `dist/duet-desktop/duet-desktop` (`.exe` on Windows). The application stores its settings and history in the user's normal application data directory; received files use the configured Received folder. The Linux package can be removed with `sudo apt remove duet`.

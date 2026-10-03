# Bunney POS

A local, offline desktop point-of-sale and accounting app for a small café.
Python 3, PyQt6 and SQLite. It runs on Windows and Linux, needs no internet
connection, and keeps everything on the machine it's installed on — no cloud, no
API server, no telemetry.

The interface is Arabic and right-to-left, since that's who it was built for.
Prices are stored as integers in minor units and are tax-inclusive, so what's on
the button is what the customer pays.

---

## Key Features

**Fast touch interface**
A responsive product grid that reflows to whatever screen it's on. Every control
is sized for a finger, not a mouse — category rail on the reading side, live cart
on the other. Tapping a drink opens a modifier popup for size, milk, sugar, shots,
extras and free-text notes; tapping water just adds it.

**Offline-first SQLite database**
One local database file, WAL mode, parameterised queries throughout. Backups go
through SQLite's online backup API, so a copy taken mid-sale is still a valid
database. No network calls anywhere in the app.

**Role permissions (Cashier / Admin)**
Two roles, enforced in one place. A cashier can sell, take payment and run a
shift. An admin additionally manages the menu, prices, inventory, reports and
backups. Cost, margin and profit figures are never rendered on the cashier
screen.

**Shift management**
Opening a shift requires the cash float, and the till won't take money until one
is open. Closing it walks through the drawer count, shows the expected amount,
computes the over/short, writes a Z-Report, runs a backup and ends the session.
The difference is labelled in words rather than a bare signed number, because
"−500" doesn't tell you whether money is missing or extra.

**Local USB backup support**
Point it at an external drive and it writes a verified copy on every shift close
and on demand. If the drive isn't plugged in, it falls back to a folder inside
the app, tells you plainly, and carries on selling. Local copies are pruned to a
configurable count.

**ESC/POS thermal receipt printing**
Thermal printers render text with their own font, which has no Arabic glyphs — so
receipts are laid out in Qt, rasterised to 1-bit at the head width, and sent as a
raster image. If no printer is reachable, the receipt is written to disk as text
and PNG instead of failing the sale.

**Also included**
Sales reports (daily / weekly / monthly, top items, peak hours, category split,
cost of goods and margin) with CSV and print-ready HTML export; inventory with
recipes and automatic per-sale deduction; a full audit log; and a stock ledger
that always reconciles against on-hand quantities.

---

## How to Run

Needs Python 3.11 or newer.

```bash
# 1. Create a virtual environment
python -m venv .venv

# 2. Activate it, then install the dependencies
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Run it
python main.py
```

There's also a small launcher that handles the above and picks the right Qt
platform for your session:

```bash
./run.sh
```

On first launch the app creates its database and seeds a starter menu — a handful
of categories, drinks, modifiers, ingredients and recipes. It also creates one
admin and one cashier account, both of which **force you to set your own PIN at
first login**. No credentials ship with the project.

Other entry points:

```bash
python main.py --reset          # rebuild the database from the schema
python main.py --selftest       # headless database and auth check, no window
```

### Tests

The suites run headless against a scratch database, so they never touch real data:

```bash
python tests/test_phase2_ui.py
python tests/test_phase3_pos.py
python tests/test_phase4_shift_backup.py
python tests/test_phase5_admin.py
```

The `tests/render_*.py` scripts render screens to PNGs, which is how the UI was
checked without a display — useful if you're changing styles or layout.

---

## Packaging & Installation

The app runs on Windows and Linux from a single frozen build, so the target
machine needs no Python and no virtual environment.

### Where your data lives

Worth knowing before you install, because it decides what a reinstall touches:

| Mode | Database, backups, logs |
| --- | --- |
| Run from a portable folder | Beside the executable |
| Installed (`.deb`, AppImage, Windows setup) | `~/.local/share/bunney-pos/` on Linux, `%LOCALAPPDATA%\bunney-pos` on Windows |

An installed copy lives somewhere the user cannot write, so it keeps its data in
your home directory instead. That is why uninstalling never deletes a cafe's
sales history — the data isn't in the install directory. Delete the folder above
by hand if you really want it gone.

Override either with `BUNNEY_DATA_DIR` if you need to.

### Linux — Debian/Ubuntu package

```bash
./packaging/build-deb.sh
sudo apt install ./dist/bunney-pos_1.0.0_amd64.deb
```

Adds a menu entry and a desktop icon, and installs to `/opt/bunney-pos`. Remove
it with `sudo apt remove bunney-pos`.

### Linux — AppImage (any distro)

```bash
./packaging/build-appimage.sh
chmod +x dist/BunneyPOS-1.0.0-x86_64.AppImage
./dist/BunneyPOS-1.0.0-x86_64.AppImage
```

One file, no install, no root. Handy for trying it on a machine before
committing to an install.

### Windows — one command

PyInstaller cannot cross-compile, so this must run on Windows. Everything else
is automatic:

```bat
build-windows.bat
```

That creates the virtual environment, installs the dependencies, regenerates the
icon, builds `dist\windows\BunneyPOS.exe`, and — if Inno Setup is installed —
also produces `dist\windows\BunneyPOS_v1.0.0_Setup.exe`.

**Installer password:** The setup file is password-protected. The password is
read at build time from the `SETUP_PASSWORD` environment variable or a `.env`
file in the project root. It is never stored in `installer.iss` or the build
script.

To set it:

```bat
set SETUP_PASSWORD=your_password_here
build-windows.bat
```

Or create a `.env` file in the project root:

```
SETUP_PASSWORD=your_password_here
```

To do the build by hand instead:

```powershell
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt pyinstaller
.venv\Scripts\python tools\make_icons.py          # logo.svg -> logo.ico
.venv\Scripts\pyinstaller --clean --noconfirm windows_build.spec
```

The installer script is [Inno Setup](https://jrsoftware.org/isdl.php). Open
`installer.iss` and press Compile, or from a terminal:

```powershell
$env:SETUP_PASSWORD = "your_password_here"
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" /DSetupPassword="$env:SETUP_PASSWORD" installer.iss
```

`dist\windows\BunneyPOS_v1.0.0_Setup.exe` installs to `Program Files\Bunney POS`,
adds a Start Menu entry and an optional desktop shortcut, and registers an
uninstaller in Apps & Features.

### Moving the project to Windows

Copy the folder and run `build-windows.bat`. Nothing needs editing by hand: the
paths are resolved at runtime, the `.ico` is committed, and the spec already
carries the Qt pruning. The only requirement is Python 3.11 or newer.

Two things worth knowing:

- **Do not copy `.venv\`** — a virtual environment is not portable between
  machines or platforms. Let the script rebuild it. `.gitignore` already
  excludes it.
- **The database is not in the repo.** A fresh install seeds itself with the
  sample menu on first launch. To move real sales data across, copy
  `assets/database/bunney_pos.db` into the data directory (see the table above)
  while the app is closed.

### Building a Linux release

```bash
./build-linux.sh            # frozen bundle in dist/linux/
./packaging/build-deb.sh    # .deb
./packaging/build-appimage.sh   # AppImage
```

Build on the oldest distribution you intend to support — the bundle links
against the build machine's glibc.

### Output layout

Windows and Linux builds write to separate directories so they never mix:

```
dist/
├── linux/                  # Linux bundle (from build-linux.sh)
│   ├── bunney-pos
│   ├── run-bunney-pos.sh
│   └── _internal/
├── windows/                # Windows build (from build-windows.bat)
│   ├── BunneyPOS.exe
│   └── BunneyPOS_v1.0.0_Setup.exe
├── bunney-pos_1.0.0_amd64.deb
└── BunneyPOS-1.0.0-x86_64.AppImage
```

Copy only `dist\windows\` when deploying to a Windows café.

---

## Project Layout

The layers are kept apart on purpose: `models/` and `controllers/` contain no Qt
imports at all, which is why the money, auth, stock and reporting logic is all
testable without a display.

```
bunney_pos/
├── main.py            entry point (GUI, --selftest, --screenshot, --reset)
├── config.py          constants, money/tax helpers, theme palettes
├── run.sh             launcher
├── database/          connection manager, transactions, migrations, schema.sql
├── models/            users, products, orders, shifts, inventory
├── controllers/       auth, POS, shifts, backups, admin — Qt-free
├── views/             PyQt6 UI (login, cashier, admin)
├── services/          ESC/POS printing, report export
├── tests/             four test suites plus render scripts
├── packaging/         .deb and AppImage builders, .desktop entry
├── assets/            stylesheets, icons, local database
├── bunney_pos.spec    PyInstaller spec (Linux)
├── windows_build.spec PyInstaller spec (Windows)
└── installer.iss      Inno Setup script (Windows installer)
```

---

## Notes

A few decisions that aren't obvious from the code:

- **Money is integer minor units, never float.** For a currency without a
  practical subunit the base unit is one whole unit. Setting
  `MINOR_UNITS_PER_MAJOR = 100` and `MONEY_DECIMALS = 2` switches it to a
  subdivided currency.
- **Tax is inclusive and derived, not added.** The order total is what the
  customer pays; the tax portion is calculated out of it for reporting only.
- **One shift at a time**, enforced by a partial unique index — this assumes a
  single till per install.
- **Restore is manual on purpose.** There's no one-tap restore, because
  overwriting a live till database from inside the app is how a day's sales get
  lost to a mis-tap. The app prints copy-the-file instructions instead.
- **No charting library.** The bar charts are hand-drawn with `QPainter`, which
  avoids a large dependency and renders Arabic labels correctly out of the box.
- **Reports export to HTML, not PDF.** Any browser can print the HTML to PDF
  offline, with no extra dependency or embedded font.

Data files (the database, backups, receipts, logs and exported reports) are
created owner-readable only, since they contain sales history and PIN hashes.

---

## License

MIT — see [LICENSE](LICENSE).

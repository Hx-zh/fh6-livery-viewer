# FH6 Livery Viewer (FH6 涂装查看器)

**English | [中文](README.md)**

Browse the liveries in your local *Forza Horizon 6* save outside the game: a tiled thumbnail wall that shows the **exact car** and the **creator** of every livery, plus a zoomable full-size preview. It fixes the two big weaknesses of the in-game livery manager — no car model shown, no vinyl preview.

**Read-only tool — it never modifies any save file.**

## Features

- 🖼️ **Tiled thumbnails**: reads the `bigThumb` previews stored in the save itself and lays all liveries out as a card wall
- 🏎️ **Real car names**: ships with a 660-car ID table (aligned entry-by-entry with in-game data; re-checked against game 6.420.696.0 for v1.3.0 — no new cars, 9 model-year fixes per the in-game ModelShort year suffixes) — no more guessing from a bare number
- 📍 **In-game position**: every livery is labeled with its row/column in the game's "My Liveries" grid (thumbnail badge + details), and the wall can follow the in-game order
- 🎯 **Auto-locate**: the details panel shows the shortest arrow-key path from row 1 / column 1 (using edge wrap-around), and the "auto-locate" button brings the game to the foreground and sends those keystrokes, landing exactly on the chosen livery
- 📌 **Always on top**: pin the window above the game so you can look up and locate liveries without leaving it
- 🔄 **Duplicate detection**: perceptual hashing plus car/creator/name-similarity rules, split into four scenarios — same-car replicas, same-author variants (v1/v2), cross-car ports, no-image name twins — freely combinable filter switches; analysis runs on demand only (zero cost when unused)
- 🟢 **Applied-livery detection**: while the game is running, a read-only scan of its process memory (no writes, no hooks, no debugger) flags which liveries are currently applied to your cars — three independent toggles: mark applied status (「标记喷涂状态(喷漆角标)」 — paints a spray-can badge onto the top-left corner of an applied livery's thumbnail, amber `#E69F00`, colorblind-friendly), show applied only, show unapplied only (the two filters are mutually exclusive), plus an applied-status line in the details panel and a status-bar count; requires the game to be running. The「⚠ 检测喷涂状态」button in the toolbar runs the whole flow in one click: it shows the mechanism/risk confirmation box, starts the scan on confirm, turns on the spray-can badge marking automatically, and pops「已标记喷涂」when done (or a prompt if the game is not running); opening any applied toggle before the first scan of a session pops the same confirmation box — cancel it and the toggle reverts with no effect; once scanned, the toggles take effect directly without further popups
- ⚡ **Performance** (v1.3.0): incremental card-wall relayout (filtering/sorting/refresh no longer destroys and rebuilds widgets — about 2s saved per rebuild), pooled background thumbnail decoding (180 cards in ~0.36s without freezing the UI), parallelized duplicate detection (3.74× measured), and a faster parallel full memory scan (steady ~3.9s)
- 🔍 **Memory-verified car list** (v1.3.1): added `gamemem.read_car_strings()` to read the in-memory `Data_Car` DisplayName / ModelShort string table while the game is running; `cars.json` was regenerated/rechecked from it, fixing several car-name issues. Also improved thumbnail decoding failure handling by keeping the old image and using exponential retry.

- 🏭 **Manufacturer filter**: narrow the wall down by make (Porsche, Ferrari, …)
- 🔍 **Zoomable preview**: open the full-size viewer from the card's right-click menu ("查看缩略图") or the details panel — wheel zoom (5%–1200%), drag panning, fit-to-window / actual-size
- 📅 **Sort by download date**: order by the moment a livery was written into your save (newest/oldest first), or by name / car / creator / in-game order
- 🔎 **Search**: filter by livery name, creator, or car
- 📋 **Native copy**: selectable details text (Ctrl+C / context menu); card context menu copies position / name / car / creator
- 💾 **One-click backup**: zip the whole save directory (a read-only operation)
- 🖥️ **Both PC editions**: auto-detects Steam saves (`userdata`) and Microsoft Store / Xbox app saves (`XboxGames\GameSave\pgs`)

## Requirements

- Windows 10/11
- Python 3.10+
- [Pillow](https://pypi.org/project/Pillow/) (`pip install pillow`) — needed to decode the WebP thumbnails used by FH6 saves; the app runs without it but livery previews won't render

## Quick Start

```bash
pip install pillow
python app.py
```

On launch the tool locates your FH6 save automatically (via the Steam registry key, or by scanning `XboxGames\GameSave\pgs` for the Store/Xbox edition). You can also point it at a `remote` or `ContainersRoot` folder manually via “手动选择目录…”.

## Packaging

```bash
python -m venv .venv-build
.venv-build\Scripts\pip install pyinstaller pillow
.venv-build\Scripts\python -m PyInstaller FH6LiveryViewer.spec
```

Produces a single-file `dist\FH6LiveryViewer.exe` with `cars.json` embedded (read-only at runtime, never extracted next to the exe).

> Note: use `python -m PyInstaller` rather than `.venv-build\Scripts\pyinstaller.exe` — the launcher shim may be broken (it exits silently with no output), while the module entry point works fine.

> The UI is currently in Simplified Chinese (button labels are quoted where relevant below); an English UI may follow.

## Usage

| Action | Result |
|---|---|
| Click a card | Select it; the right panel shows the large preview and metadata (name / car / creator / in-game position / key path / date / layers / size / duplicate group) |
| Double-click a card | Auto-locate that livery in the game (same as the "auto-locate" button in the details panel) |
| Right-click a card | Context menu: view thumbnail / locate livery in game / copy in-game position, name, car, creator |
| Ctrl+C | Copies the selected text when the details box is focused; otherwise copies the selected livery's in-game position |
| 「涂装筛选」 (Filters) | All filters are independent toggles that stack (multiple scenarios OR together); duplicate analysis starts in the background the first time any toggle is switched on — zero cost otherwise |
| 「置顶」 (Always on top) | Keep the window above the game for quick access |
| 「自动定位到游戏」 (Auto-locate) | Instantly brings the game window to the foreground and sends arrow keys to jump to the selected livery (shows a notice if the game is not running); click again or press Esc to cancel mid-send. Requires a freshly opened "My Designs" screen (focus at row 1 / column 1) with no in-game filter applied |
| 「设置」 (Settings) | Tunes the auto-locate key timing (key hold + inter-key gap, in ms); applies to the current session only — nothing is written to disk or the registry, keeping the single-file exe footprint-free. The measured per-key cycle threshold is ~30ms — anything below drops keys; default is 15+25=40ms, raise it on low-FPS machines |
| 「⚠ 检测喷涂状态」 (Detect applied status) | Shows the mechanism/risk confirmation box for the applied-livery memory scan; on confirm it starts the scan and turns on the spray-can badge marking automatically, then pops「已标记喷涂」when done (or prompts you to launch the game first if it is not running). Opening any applied toggle (mark applied / applied only / unapplied only) before the first scan of a session pops the same confirmation box — cancel it and the toggle reverts with no effect; once scanned, toggles take effect directly with no further popups |
| 「车厂」 (Manufacturer) | Filter by make |
| 「备份整个存档」 (Backup) | Zip the save directory into `backups\` |
| 「所在文件夹」 (Open folder) | Reveal the livery in Explorer |

Card rows: livery name / car name (wraps to show in full) / creator. If the car is not in the table, the third row shows `ID xxxx + date` instead. The badge at the thumbnail's top-right is the in-game position (`N行M列` — row N of column M; "My Liveries" groups by car, two rows per column); with applied-status marking on, applied liveries also get a spray-can badge at the top-left — the two badges coexist side by side.

## Save Format Notes

The tool is **read-only**. These findings come from real on-disk saves plus community reverse engineering:

- **Steam**: `<Steam>\userdata\<user id>\<appid>\remote\` (FH6 appid `2483190`). Each livery is a set of same-name part files: `Livery_<car id>_<timestamp>.header / .C_livery / .bigThumb.png …`
- **Microsoft Store / Xbox app (pgs)**: `<drive>:\XboxGames\GameSave\pgs\u_<id>\<snapshot>\ContainersRoot\`. Each livery is a same-named **folder** containing `header` / `C_livery` / `bigThumb.webp`. The `current` junction points at the active snapshot and the snapshot number rotates (100 → 101 …); the tool follows it automatically
- `header` is binary metadata (version / name / creator / date / layer count / car ID); byte layout documented in `fh6save.py` comments
- The filename timestamp is UTC; the UI displays it converted to local time

## Project Structure

```
├── app.py      # GUI application (read-only viewer)
├── fh6save.py  # save scanning / header parsing library; standalone self-check: python fh6save.py
├── gamemem.py  # read-only game process memory scanner (applied-livery detection / car string-table reader)
├── cars.json   # car ID → name table (embedded into the exe at build time, read-only at runtime)
└── LICENSE     # AGPL-3.0
```

## Upstream & Credits

The save-header parsing logic is based on the reverse-engineering work of the upstream project
**[Arstz/FH6_livery_unlocker](https://github.com/Arstz/FH6_livery_unlocker)**, corrected and extended against
real local FH4/FH5/FH6 saves (the FH6 header v7 layer-count / car-ID offsets were contributed by this project).
Following the upstream, this project is licensed under **AGPL-3.0** as well.

The car ID table originally came from [HDR's FH6 Car Ordinals](https://gist.github.com/HDR/0659d1717bc61504bf83750628963f4f) and has since been aligned entry-by-entry with the game's `Data_Car.str` string table (660 cars).

## Disclaimer

This tool is not affiliated with Microsoft, Xbox, Playground Games, or Turn 10. Forza and related trademarks belong to their respective owners. It only reads local content (local save files and, while the game is running, a read-only scan of the game process memory for applied-livery detection; no writes, no hooks). It provides no modification, unlocking, or online functionality. Use at your own risk.

## License

[GNU Affero General Public License v3.0](LICENSE) — kept consistent with the upstream project.

# FH6 Livery Viewer (FH6 涂装查看器)

**English | [中文](README.md)**

Browse the liveries in your local *Forza Horizon 6* save outside the game: a tiled thumbnail wall that shows the **exact car** and the **creator** of every livery, plus a zoomable full-size preview. It fixes the two big weaknesses of the in-game livery manager — no car model shown, no vinyl preview.

**Read-only tool — it never modifies any save file.**

## Features

- 🖼️ **Tiled thumbnails**: reads the `bigThumb` previews stored in the save itself and lays all liveries out as a card wall
- 🏎️ **Real car names**: ships with a 660-car ID table (aligned with in-game data) — no more guessing from a bare number
- 📍 **In-game position**: every livery is labeled with its row/column in the game's "My Liveries" grid (thumbnail badge + details), and the wall can follow the in-game order
- 🔄 **Duplicate detection**: perceptual hashing plus car/creator/name-similarity rules, split into four scenarios — same-car replicas, same-author variants (v1/v2), cross-car ports, no-image name twins — each filterable on its own
- 🏭 **Manufacturer filter**: narrow the wall down by make (Porsche, Ferrari, …)
- 🔍 **Zoomable preview**: double-click for a full-size viewer with wheel zoom (5%–1200%), drag panning, fit-to-window / actual-size
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
| Click a card | Select it; the right panel shows the large preview and metadata (name / car / creator / in-game position / date / layers / size / duplicate group) |
| Double-click a card | Open the zoomable preview window |
| Right-click a card | Context menu: copy in-game position / name / car / creator |
| Ctrl+C | Copies the selected text when the details box is focused; otherwise copies the selected livery's in-game position |
| 「重复涂装筛选」 (Dup filter) | Single-choice scenario filter (click the active item again to clear); can stack with the multi/single-livery-per-car dimension |
| 「车厂」 (Manufacturer) | Filter by make |
| 「备份整个存档」 (Backup) | Zip the save directory into `backups\` |
| 「所在文件夹」 (Open folder) | Reveal the livery in Explorer |

Card rows: livery name / car name (wraps to show in full) / creator. If the car is not in the table, the third row shows `ID xxxx + date` instead. The badge at the thumbnail's top-right is the in-game position (`N行M列` — row N of column M; "My Liveries" groups by car, two rows per column).

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

This tool is not affiliated with Microsoft, Xbox, Playground Games, or Turn 10. Forza and related trademarks belong to their respective owners. It only reads local save files and provides no modification, unlocking, or online functionality. Use at your own risk.

## License

[GNU Affero General Public License v3.0](LICENSE) — kept consistent with the upstream project.

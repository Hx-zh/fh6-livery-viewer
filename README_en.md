# FH6 Livery Viewer (FH6 涂装查看器)

**English | [中文](README.md)**

Browse the liveries in your local *Forza Horizon 6* save outside the game: a tiled thumbnail wall that shows the **exact car** and the **creator** of every livery, plus a zoomable full-size preview. It fixes the two big weaknesses of the in-game livery manager — no car model shown, no vinyl preview.

**Read-only tool — it never modifies any save file.**

## Features

- 🖼️ **Tiled thumbnails**: reads the `bigThumb` previews stored in the save itself and lays all liveries out as a card wall
- 🏎️ **Real car names**: ships with a 651-car ID table (see Upstream & Credits) — no more guessing from a bare number
- 🔍 **Zoomable preview**: double-click for a full-size viewer with wheel zoom (5%–1200%), drag panning, fit-to-window / actual-size
- 📅 **Sort by download date**: order by the moment a livery was written into your save (newest/oldest first), or by name / car / creator
- 🔎 **Search**: filter by livery name, creator, or car
- 🏷️ **Car tagging**: name unrecognized car IDs yourself; stored in `cars.json`
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

or double-click `启动.bat`.

On launch the tool locates your FH6 save automatically (via the Steam registry key, or by scanning `XboxGames\GameSave\pgs` for the Store/Xbox edition). You can also point it at a `remote` or `ContainersRoot` folder manually via “手动选择目录…”.

> The UI is currently in Simplified Chinese (button labels are quoted where relevant below); an English UI may follow.

## Usage

| Action | Result |
|---|---|
| Click a card | Select it; the right panel shows the large preview and metadata (name / car / creator / date / layers / size) |
| Double-click a card | Open the zoomable preview window |
| 「标注车型…」 (Tag car) | Assign a name to an unrecognized car ID (writes to `cars.json`, save untouched) |
| 「备份整个存档」 (Backup) | Zip the save directory into `backups\` |
| 「所在文件夹」 (Open folder) | Reveal the livery in Explorer |

Card rows: livery name / car name / creator. If the car is not in the table, the third row shows `ID xxxx + date` instead.

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
├── cars.json   # car ID → name table (editable; user tags win)
└── LICENSE     # AGPL-3.0
```

## Upstream & Credits

The save-header parsing logic is based on the reverse-engineering work of the upstream project
**[Arstz/FH6_livery_unlocker](https://github.com/Arstz/FH6_livery_unlocker)**, corrected and extended against
real local FH4/FH5/FH6 saves (the FH6 header v7 layer-count / car-ID offsets were contributed by this project).
Following the upstream, this project is licensed under **AGPL-3.0** as well.

The car ID table comes from [HDR's FH6 Car Ordinals](https://gist.github.com/HDR/0659d1717bc61504bf83750628963f4f) (2026-07-14 snapshot, 651 cars).

## Disclaimer

This tool is not affiliated with Microsoft, Xbox, Playground Games, or Turn 10. Forza and related trademarks belong to their respective owners. It only reads local save files and provides no modification, unlocking, or online functionality. Use at your own risk.

## License

[GNU Affero General Public License v3.0](LICENSE) — kept consistent with the upstream project.

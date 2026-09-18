# FH6 Livery Viewer (FH6 涂装查看器)

**English | [中文](README.md)**

Browse the liveries in your local *Forza Horizon 6* save outside the game: a tiled thumbnail wall that shows the **exact car** and the **creator** of every livery, plus a zoomable full-size preview. It fixes the two big weaknesses of the in-game livery manager — no car model shown, no vinyl preview.

**Read-only tool — it never modifies any save file.**

## Features

- 🖼️ **Tiled thumbnails**: reads the `bigThumb` previews stored in the save itself and lays all liveries out as a card wall
- 🏎️ **Real car names**: ships with a 671-car ID table (aligned entry-by-entry with in-game data; re-checked against game 6.420.696.0 for v1.3.0 — no new cars, 9 model-year fixes per the in-game ModelShort year suffixes; manually re-verified entry-by-entry in 2026-09 — 11 new cars added, 48 existing entries fixed) — no more guessing from a bare number
- ⬇️ **Online car-table updates** (v1.8.0): the car-name table can now be updated online, **decoupling data updates from program releases** — when the game adds new cars there is no need to wait for a new build; once the maintainer publishes updated data, clients pick it up via an automatic daily check (on by default, can be turned off in Settings). Sources are ordered by your UI language — Gitee first for Simplified Chinese, GitHub first otherwise — with a CDN mirror as the final fallback. Downloads are strictly validated (size / structure / entry count); if every source fails the tool silently keeps using the local data — the built-in table always remains the fallback. No telemetry whatsoever
- 📍 **In-game position**: every livery is labeled with its row/column in the game's "My Liveries" grid (thumbnail badge + details), and the wall can follow the in-game order
- 🎯 **Auto-locate**: the details panel shows the shortest arrow-key path from row 1 / column 1 (using edge wrap-around), and the "auto-locate" button brings the game to the foreground and sends those keystrokes, landing exactly on the chosen livery
- 📌 **Always on top**: pin the window above the game so you can look up and locate liveries without leaving it
- 🔃 **Auto refresh**: a top-bar toggle (on by default) polls the current save folder (3s poll + 2s debounce); after liveries are downloaded or deleted in-game there is no need to press 「刷新」 — new liveries are inserted into the card wall in place while the selection and scroll position are preserved (status bar: 「检测到存档更新: +N 新增 / -M 删除, 已刷新」); it can be switched off anytime, and the manual refresh button keeps its original behavior (hardened: a temporarily inaccessible save folder pauses and notifies once, then auto-resumes polling once it returns; a failed refresh rolls back and waits to retry; entries whose header failed to parse are re-tried in the background and self-heal once fully written)
- 🔄 **Duplicate detection**: pairwise matching via perceptual hashing with no preset rules — the judgment is a single condition you compose yourself in the「⚙ 重复检测参数」(duplicate-detection parameters) button at the right end of the filter bar. Seven dimensions stack arbitrarily: car model same/different (default same), creator any/same/different (default any), image comparison off / perceptual-hash distance ≤ N / either side lacks its preview file, name similarity off / ≥ X / < X, creation and download time each ignored/same/different, and layer count same/different (new in v1.8.0 — equal layer counts are a strong duplicate signal, but unrelated designs collide on counts often, so pair it with same-car/same-creator); a pair counts as duplicated when every enabled condition matches, and groups merge transitively (A~B, B~C ⇒ all three share a group). Factory default = same car model AND image distance ≤ 6. A template dropdown fills the form with four classic patterns in one click (同车微调 same-car v1/v2 tweaks / 跨车型移植 cross-car port / 同名异版 same-name revisions / 多次下载 repeat downloads) — form fillers only, the engine still evaluates just your combined rule; the dialog reopens with the last applied values pre-filled, auto-selects the template name when the current rule matches one exactly, and offers a restore-default button. Confirming recomputes instantly on cached features (milliseconds) and auto-checks "show duplicates only" so the result is immediately visible (the condition itself lasts for the current session only); analysis runs on demand only (zero cost when unused). v1.8.0 also fixes the "creation time" condition to compare the creator's true creation time embedded in the livery file (it previously compared download times), making the same-name-revisions and repeat-downloads templates more accurate
- 🗂️ **Duplicate-group display**: with 仅显示重复涂装 (show duplicates only) on, the wall pulls each duplicate group together under a full-width group header row (group number and member count); the sort mode applies both within groups and across groups
- 🟢 **Applied-livery detection** (reworked in v1.8.0): reads the "on-register" list inside the game's thumbnail-cache manifest (`CacheThumbnails/.manifest`) to tell which liveries are currently applied to your cars — no game running, no memory scan, zero risk. Applied liveries are marked automatically right at startup and on every save switch (zero clicks, zero confirmations, zero popups) with a spray-can badge (amber `#E69F00`, top-left corner of the card, colorblind-friendly; visible for the rest of the session — no toggle needed — and gone when you switch saves), and applying/removing a livery in-game is followed automatically within ~3 seconds. If the manifest appears only later (cache was missing at startup), the poller picks it up automatically; the old memory-scan fallback only ever triggers when you enable an applied filter while the manifest is unavailable (mechanism/risk confirmation box, then a read-only scan of the running game's process memory — no writes, no hooks, no debugger). The top-bar button was removed in v1.8.0: detection is fully automatic. Two independent filter toggles remain:「仅显示已喷涂(在车上)」(show applied only) and「仅显示未喷涂(不在车上)」(show unapplied only; the two are mutually exclusive), plus an applied-status line in the details panel and a status-bar count; when the manifest is unavailable the status shows "pending" — it is never mistaken for "not applied"
- 🏷️ **Auction liveries (SoulBoundLivery)**: displays liveries bought from the auction house. They live in `SoulBoundLivery` containers that hold **no** thumbnail file, so the tool locates the matching `GUID.webp` in the game's `CacheThumbnails` cache (shows「(无预览图)」/ no preview when the cache is missing; fixed in v1.8.0 — when several candidate images exist for the same livery the tool no longer leaves the card blank but picks the best one: the one currently applied to a car if any, else the most recently written). Auction cards carry an auction-gavel badge at the top-left (gray by default = not applied / not detected; turns amber `#E69F00` once it is confirmed on a car). The「涂装筛选」(filters) menu adds two mutually exclusive source toggles —「仅显示拍卖涂装」/ "show only auction liveries" and「只显示非拍卖涂装」/ "show only non-auction liveries" — plus「显示无预览图的拍卖涂装」/ "show auction liveries without a preview" (off by default; auction liveries with no matched preview are hidden unless this is on), plus two mutually exclusive toggles —「仅显示已应用拍卖涂装(在车上)」/ show applied auction liveries (on car) and「仅显示未应用拍卖涂装(不在车上)」/ show unapplied auction liveries (not on car) — which are mutually exclusive with the applied/unapplied toggles and prefer the automatic cache-manifest check when undetected, falling back to the same confirmation gate only if the manifest is unavailable. The right-click menu adds a source quick filter:「只显示拍卖涂装」(show only auction liveries) on auction cards and「只显示我的设计」(show only My Designs liveries) on normal cards. Because FH6 doesn't store the original title/creator for auction liveries, their name row reads「拍卖涂装」and the creator shows "?", and they have no in-game position (cannot be auto-located)
- ⚡ **Performance**: the card wall is drawn virtually on a single canvas (v1.4.0 rework) — smooth, flicker-free scrolling; pooled background thumbnail decoding (180 cards in ~0.36s without freezing the UI), parallelized duplicate detection (3.74× measured), and a faster parallel full memory scan (steady ~3.9s)
- 🔍 **Memory-verified car list** (v1.3.1): added `gamemem.read_car_strings()` to read the in-memory `Data_Car` DisplayName / ModelShort string table while the game is running; `cars.json` was regenerated/rechecked from it, fixing several car-name issues. Also improved thumbnail decoding failure handling by keeping the old image and using exponential retry. `gamemem.read_car_years()` additionally reads the game's runtime full car-name strings (e.g. "拍摄 1962 年份的 Ferrari 250 GTO") and applied 52 authoritative year fixes to the table in 2026-08.
- 🌐 **Multilingual UI** (v1.6.0): Simplified Chinese (default), English, 日本語, 한국어, and 繁體中文 — the exe picks its UI language from its own file name, so just download the zip for your language (e.g. `FH6LiveryViewer_en.exe` runs in English); no settings to change
- 🔤 **Consolas for user data** (v1.6.0): livery names, car names, and creators on the cards and in the details panel are rendered in Consolas, where `I` and `l` are clearly distinguishable (in Microsoft YaHei both are bare vertical strokes); CJK / kana / Hangul text falls back through Windows font linking with no missing glyphs; the three card text rows were bumped up one point (name 10pt bold, car/creator 9pt) and the cards themselves enlarged for readability

- 🏭 **Manufacturer filter**: narrow the wall down by make (Porsche, Ferrari, …)
- 🔍 **Zoomable preview**: open the full-size viewer from the card's right-click menu ("查看缩略图") or the details panel — wheel zoom (5%–1200%), drag panning, fit-to-window / actual-size
- 📅 **Sort by download date**: order by the moment a livery was written into your save (newest/oldest first), or by name / car / creator / brand / in-game order; a secondary sort dropdown ("无" / none by default) acts as the second key, and it also applies within and between duplicate groups
- 🗂️ **Group-by display**: group the card wall by brand, creator, or car model (a full-width header row shows each group's name and count; group order follows the current sort; items without a value fall into an "其他"/others group that is always kept last; when grouping by car the header uses the car's display name and unrecognized cars form their own "ID xxx" group); selecting any dimension switches the primary sort to match automatically
- 🔎 **Search**: filter by livery name, creator, or car; the two search boxes (joined by a "+" label) form an AND — when both keywords are filled in, both must match. Leaving the second box empty behaves like the old single-keyword search
- ⚡ **Right-click quick filter**: a card's context menu offers「只显示该车型」(show this car only) and「只显示该作者」(show this creator only) to narrow the wall in one click; while active the filter button text gains a「快筛」segment and the same menu shows 清除快筛 (clear quick filter) at the top to restore (cleared automatically on save switch)
- 📋 **Native copy**: selectable details text (Ctrl+C / context menu); card context menu copies position / name / car / creator
- 💾 **One-click backup**: zip the whole save directory (a read-only operation)
- 🖥️ **Both PC editions**: auto-detects Steam saves (`userdata`) and Microsoft Store / Xbox app saves (`XboxGames\GameSave\pgs`)

## Requirements

- Windows 10/11
- Python 3.10+
- [Pillow](https://pypi.org/project/Pillow/) (`pip install pillow`) — needed to decode the WebP thumbnails used by FH6 saves; the app runs without it but livery previews won't render

## Download

Each [release](https://github.com/Hx-zh/fh6-livery-viewer/releases) provides five zips; the only difference is the UI language (the exe picks it from its own file name):

| zip (`vX.Y.Z` = version; language tag is BCP 47) | UI language |
|---|---|
| `FH6LiveryViewer_vX.Y.Z_win64_zh-CN.zip` | 简体中文 (Simplified Chinese) |
| `FH6LiveryViewer_vX.Y.Z_win64_zh-TW.zip` | 繁體中文 (Traditional Chinese) |
| `FH6LiveryViewer_vX.Y.Z_win64_en.zip` | English |
| `FH6LiveryViewer_vX.Y.Z_win64_ja.zip` | 日本語 (Japanese) |
| `FH6LiveryViewer_vX.Y.Z_win64_ko.zip` | 한국어 (Korean) |

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

Produces a single-file `dist\FH6LiveryViewer.exe` with `cars.json` embedded (read-only at runtime, never extracted next to the exe; since v1.8.0 the online-updated car table is cached under `%LOCALAPPDATA%\FH6LiveryViewer\`). To produce a language build, build once and then copy/rename the exe with the matching BCP 47 tag — `FH6LiveryViewer_zh-CN.exe` / `_zh-TW.exe` / `_en.exe` / `_ja.exe` / `_ko.exe`: the exe picks its UI language from its own file name (during development you can also override it with the `FH6_LANG` environment variable, e.g. `FH6_LANG=en`).

> Note: use `python -m PyInstaller` rather than `.venv-build\Scripts\pyinstaller.exe` — the launcher shim may be broken (it exits silently with no output), while the module entry point works fine.

> The UI is multilingual since v1.6.0: Simplified Chinese (default), English, 日本語, 한국어, and 繁體中文 — the exe picks its UI language from its own file name (see the Download table above). Chinese button labels are quoted where relevant below.

## Usage

| Action | Result |
|---|---|
| Click a card | Select it; the right panel shows the large preview and metadata (name / car / creator / in-game position / key path / created & downloaded time / layers / size / duplicate group) — since v1.8.0 the single "date" row is split into two: creation time (the creator's true creation time embedded in the livery file) and download time (when you downloaded/saved it), each shown as `YYYY-MM-DD HH:MM:SS (UTC±hh:mm)` with its timezone suffix |
| Double-click a card | Auto-locate that livery in the game (same as the "auto-locate" button in the details panel) |
| Right-click a card | Context menu: view thumbnail / locate livery in game / show this car only, show this creator only (right-click quick filter; while active, 清除快筛 at the top of the same menu restores) / copy in-game position, name, car, creator |
| Ctrl+C | Copies the selected text when the details box is focused; otherwise copies the selected livery's in-game position |
| 「涂装筛选」 (Filters) | All filters are independent toggles that stack; duplicate analysis starts in the background the first time any toggle depending on it is switched on — zero cost otherwise. The duplicate judgment condition itself can be freely tuned in the「⚙ 重复检测参数」(duplicate-detection parameters) dialog. Includes the auction source toggles —「仅显示拍卖涂装」(show only auction liveries) and「只显示非拍卖涂装」(show only non-auction liveries, mutually exclusive with the former) — plus「显示无预览图的拍卖涂装」(show auction liveries without a preview; off by default, and auction liveries with no matched preview are hidden unless it is on), and the two auction-livery applied-status toggles —「仅显示已应用拍卖涂装(在车上)」/ show applied auction liveries (on car) and「仅显示未应用拍卖涂装(不在车上)」/ show unapplied auction liveries (not on car) — mutually exclusive with each other and with the applied/unapplied toggles; when undetected they prefer the automatic cache-manifest check and only fall back to the confirmation gate (memory scan on confirm, revert on cancel) if the manifest is unavailable |
| 「置顶」 (Always on top) | Keep the window above the game for quick access |
| "Auto-detect save updates" | Toggle inside Settings (on by default, persisted locally): when the save folder changes (liveries downloaded or deleted in-game) the wall refreshes incrementally — new cards are inserted in place while the selection and scroll position stay put; switching it off returns to manual-only, and the 「刷新」 button keeps its original behavior |
| 「自动定位到游戏」 (Auto-locate) | Instantly brings the game window to the foreground and sends arrow keys to jump to the selected livery (shows a notice if the game is not running); click again or press Esc to cancel mid-send. Requires a freshly opened "My Designs" screen (focus at row 1 / column 1) with no in-game filter applied |
| 「设置」 (Settings) | Tunes the auto-locate key timing (key hold + inter-key gap, in ms); saved on confirm to `%LOCALAPPDATA%\FH6LiveryViewer\config.json` and kept across restarts, nothing in the registry); the "Auto-detect save updates" toggle (on by default, takes effect immediately and persists locally). The measured per-key cycle threshold is ~30ms — anything below drops keys; default is 15+50=65ms (relaxed from 40ms after reports of dropped keys on low-FPS machines), raise it further if needed. Also contains a "Car name table (online update)" section: two info lines for the built-in and online tables (entry count, update date and source; whichever is active is tagged "(in use)"), the checkbox "Check for online updates daily (Gitee/GitHub)" (persisted in the state file under the `%LOCALAPPDATA%` cache directory; key timing is stored as config.json in the same folder), and two buttons — "Check for car-table updates" (manual check now, result shown in a popup, not limited to once a day) and "Restore built-in data" (reload the embedded table and clear the online cache); an "Open config folder" button at the bottom opens `%LOCALAPPDATA%\FH6LiveryViewer\` in Explorer (the three data files live there) |
| 「车厂」 (Manufacturer) | Filter by make |
| 「备份整个存档」 (Backup) | Zip the save directory into `backups\` |
| 「所在文件夹」 (Open folder) | Reveal the livery in Explorer |

Card rows: livery name / car name (wraps to show in full) / creator. If the car is not in the table, the third row shows `ID xxxx + date` instead. The badge at the top-right of the card's thumbnail area is the in-game position (`N行M列` — row N of column M; "My Liveries" groups by car, two rows per column); applied liveries also get a spray-can badge at the top-left of the card (marked automatically at startup / on save switch from the cache manifest since v1.8.0; stays for the rest of the session, no toggle needed) — the two badges coexist side by side. **Auction-livery cards** have no in-game-position badge (they are not in the "My Liveries" grid); instead their top-left badge is an auction gavel (gray by default = not applied / not detected, turning amber once it is confirmed on a car, replacing the spray-can badge). Their name row reads「拍卖涂装」and the creator shows "?" (FH6 doesn't store the original title/creator).

## Save Format Notes

The tool is **read-only**. These findings come from real on-disk saves plus community reverse engineering:

- **Steam**: `<Steam>\userdata\<user id>\<appid>\remote\` (FH6 appid `2483190`). Each livery is a set of same-name part files: `Livery_<car id>_<timestamp>.header / .C_livery / .bigThumb.png …`
- **Microsoft Store / Xbox app (pgs)**: `<drive>:\XboxGames\GameSave\pgs\u_<id>\<snapshot>\ContainersRoot\`. Each livery is a same-named **folder** containing `header` / `C_livery` / `bigThumb.webp`. The `current` junction points at the active snapshot and the snapshot number rotates (100 → 101 …); the tool follows it automatically
- **Auction liveries (SoulBoundLivery)**: liveries bought from the auction house live in `SoulBoundLivery_<car id>_<14-digit timestamp>` containers under `ContainersRoot`, and those containers hold **no** thumbnail file; their previews come from the game's `CacheThumbnails` cache (the `CacheThumbnails/.manifest` has two tables — a first GUID/materialization table and a second logical-name registry; the logical-name format is `<car id>_<16-hex instanceKey>(bm<N>|u<26-char token>)_bigThumb.webp`). The tool derives a 26-char token from the last 16 bytes of a SoulBoundLivery header via Crockford Base32, then looks it up in the first table to get `CacheThumbnails/<GUID>.webp` as the preview (the cache lives in `%LOCALAPPDATA%\ForzaHorizon6\LocalStorage_Cache\CacheThumbnails`, where Store-edition machines also land). When the cache is missing/corrupt the auction items degrade to "no preview" without affecting anything else
- `header` is binary metadata (version / name / creator / creation time / layer count / car ID); byte layout documented in `fh6save.py` comments
- The filename timestamp (UTC, shown converted to local time) is the player's download/save time; the creator's true creation time is embedded in the header (UTC, converted to local) — the two are independent (since v1.8.0 the UI shows them separately as download time / creation time)

## Project Structure

```
├── app.py      # GUI application (read-only viewer)
├── fh6save.py  # save scanning / header parsing library; standalone self-check: python fh6save.py
├── gamemem.py  # read-only game process memory scanner (applied-livery detection / car string-table reader)
├── carupdate.py # online car-table update (three-source fallback + local cache, stdlib only)
├── i18n/__init__.py  # UI localization (Chinese source string = key; FH6_LANG env var → exe file-name suffix → default zh)
├── i18n/lang_en.py / lang_ja.py / lang_ko.py / lang_zhtw.py  # translation tables (217 entries each)
├── check_i18n.py  # i18n coverage checker (run after any UI string change; releases require COVERAGE OK)
├── cars.json   # car ID → name table (embedded into the exe at build time as the fallback; supports online updates since v1.8.0)
└── LICENSE     # AGPL-3.0
```

## Upstream & Credits

The save-header parsing logic is based on the reverse-engineering work of the upstream project
**[Arstz/FH6_livery_unlocker](https://github.com/Arstz/FH6_livery_unlocker)**, corrected and extended against
real local FH4/FH5/FH6 saves (the FH6 header v7 layer-count / car-ID offsets were contributed by this project).
Following the upstream, this project is licensed under **AGPL-3.0** as well.

The car ID table originally came from [HDR's FH6 Car Ordinals](https://gist.github.com/HDR/0659d1717bc61504bf83750628963f4f) and has since been aligned entry-by-entry with the game's `Data_Car.str` string table (671 cars); it was manually re-verified entry-by-entry in 2026-09 by McEvofusion (11 new cars, 48 fixes).

## Disclaimer

This tool is not affiliated with Microsoft, Xbox, Playground Games, or Turn 10. Forza and related trademarks belong to their respective owners. It only reads local content (local save files and the game's thumbnail-cache manifest; only when the manifest is unavailable and you explicitly confirm does it fall back to a read-only scan of the game process memory while the game is running, for applied-livery detection; no writes, no hooks); apart from the online car-table update (downloading car-name data from Gitee/GitHub, no telemetry, with the automatic daily check switchable off in Settings) it accesses no network and provides no modification, unlocking, or online game functionality. Use at your own risk.

## License

[GNU Affero General Public License v3.0](LICENSE) — kept consistent with the upstream project.

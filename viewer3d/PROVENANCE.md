# Vendored Source Provenance

This directory contains a vendored snapshot of code from **Forza Livery Studio**
(https://github.com/Arstz/ForzaLiveryStudio), licensed under the
**GNU Affero General Public License v3.0** (see `fls/LICENSE`).

- Upstream commit: `cb9aa30134331f7ab06ee27a2f67ee0699354377` (2026-10-16)
- Vendored subset:
  - `fls/src/core/` — complete upstream `src/core` (livery/vinyl codecs, carbin
    model parsing, livery projection masks, paint materials, raster decals).
  - `fls/src/gui/` — compile closure of the 3D preview chain
    (`car_preview_widget`, `car_model_renderer`, `native_shape_renderer`,
    `shape_geometry_store`, `car_registry`, `gui_assets`, `theme_manager`,
    `editor_state`/`scene_view`, `fill/*` headers).
  - `fls/third_party/clipper2/` — used by `car_model_renderer`
    (Boost Software License 1.0; see its LICENSE).
  - `assets/vector/`, `assets/cars/` — vinyl shape geometry catalog and
    car-id → model-directory mapping. **These are derived data**, decoded
    from the local game files, distributed under AGPL following the upstream
    repository's precedent.
- Local modifications (tracked in this repository's history):
  - `fls/src/gui/widgets/car_preview_widget.cpp` — orbit drag sensitivity
    0.01 -> 0.005 rad/px (user feedback: default felt too twitchy); new
    `highestDetailLodIndex()` (max-triangle LOD) so the viewer defaults to
    the highest-detail model regardless of LOD ordering.
  - `fls/src/core/raster_decals.cpp` — additional cache lookup path under
    `QStandardPaths::AppDataLocation` (raster decal pack built on first run
    from the user's game files instead of shipping a 67 MB binary).
  - Built with `FLS_PRIVACY_POLICY=0`: upstream's gate refuses to import
    shared liveries (vlrc source state 1 — ~99.75% of a player's downloaded
    collection). That gate protects the upstream *editor* from re-exporting
    other creators' work; this repository builds a strictly **read-only
    viewer** of the user's own save (no editing or export code compiled in)
    that only shows what the game itself already displays locally.
- Runtime dependencies of the shipped binary (licensing notices live in
  `licenses/` and in `SOURCE.md` which is included in release zips):
  Qt 6.8.3 dynamically linked under LGPL-3.0 (source: download.qt.io /
  github.com/qt/qt5 tag v6.8.3, unmodified), zlib (zlib License), Microsoft
  D3Dcompiler_47.dll (Windows SDK redistributable terms).
- This snapshot is intentionally decoupled from upstream: it does not track
  upstream development. Patches from upstream may be applied selectively at
  this project's discretion; the snapshot itself is the source of truth here.
- This project (FH6LiveryViewer) is likewise AGPL-3.0; vendoring is license-
  compatible. The 3D viewer binary built from this directory (`FH6Livery3DViewer`)
  is a derivative work of Forza Livery Studio and is distributed under AGPL-3.0.

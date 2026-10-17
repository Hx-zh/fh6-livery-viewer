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
  - `fls/src/gui/canvas/car_model_renderer.cpp` — `kMaskTextureScale` lowered
    from 2 to 1. The livery masks shipped inside each car archive are 2048x1024
    (one 1 MB R8 swatchbin per side), so the 2x upsample only interpolated them
    onto a 4096x2048x11 R8 array: 92 MB of VRAM for no extra information
    (the shader thresholds coverage at 0.5 anyway). Now the array is 23 MB and
    holds the game's mask bytes verbatim (`upsampleCoverageMask` returns the
    source unchanged when the sizes match).
  - `fls/src/gui/canvas/native_shape_renderer.cpp` — the vinyl atlas is now
    rendered **supersampled**: the shape scene goes into a transient 2x target
    (4x canvas → 16384x8192 RGBA8, ~512 MB) and is box-filtered down into the
    8192x4096 atlas via `blitFramebuffer(..., GL_LINEAR)`. Before this the
    shapes were hard-rasterised with no coverage AA at all, so canvas
    resolution alone decided how blocky the artwork looked. The 2x target is
    freed after every rebuild; when `GL_MAX_TEXTURE_SIZE` cannot host it the
    renderer falls back to 1x. The incremental (scissor-preserving) path stays
    at 1x — only the editor's live section updates use it.
  - `fls/src/gui/widgets/car_preview_widget.cpp` — orbit drag sensitivity
    0.01 -> 0.005 rad/px (user feedback: default felt too twitchy); new
    `highestDetailLodIndex()` (max-triangle LOD) so the viewer defaults to
    the highest-detail model regardless of LOD ordering; `smoothLiveryTexture()`
    fixes hard stair-steps on every vinyl edge — the livery atlas is an FBO
    texture and Qt leaves its filters at `GL_NEAREST`, so the 8192x4096 canvas
    was sampled nearest-neighbour onto a ~1k-pixel car. Now bilinear
    magnification, mipmapped minification and anisotropic filtering (16x where
    the driver allows it — measured up to `GL_MAX_TEXTURE_MAX_ANISOTROPY`),
    regenerated only when the atlas changes. The widget's own framebuffer also
    went from 4x to 8x MSAA (verified: `GL_SAMPLES` = 8 inside `paintGL`).
  - `fls/src/core/raster_decals.cpp` — additional cache lookup path under
    `QStandardPaths::AppDataLocation` (raster decal pack built on first run
    from the user's game files instead of shipping a 67 MB binary).
  - `fls/src/core/swatchbin.cpp` — share one D3D11 device across all BC7
    decodes (creating a device per texture made the 270-decal raster pack
    build take ~27 s on first run; a shared device brings it to ~2 s).
  - `fls/src/core/vinyl_decoder.cpp` — three patches for layers disappearing
    from the preview (see AGENTS.md "C_livery 解码丢层"):
    1. `isShapePayloadAt()` keeps only the checks that separate a shape record
       from random bytes (id known, finite floats, sane position). Upstream
       additionally required `|scaleX|`/`|scaleY|` to be non-zero and
       `|skew| < 200`, which rejects records real FH6 saves contain: zero-scale,
       fully transparent placeholder layers the game still counts, and hairline
       strokes where `skew` is large because the shear is applied *after* the
       scale (`scaleY ≈ 0.001`, `skew ≈ 1500`).
    2. `walkStep()` treats the group's child-type bitmap as a hint instead of a
       hard constraint: when the type it asks for is not present at the current
       offset, the other type is tried. The bitmap drifts in real saves (one
       child consumed as a transform shifts every later bit), and insisting on
       it made the walker byte-step over whole subtrees (sample
       `Livery_3622_20261012231728` lost a 99-shape group that way).
    3. Group headers are rejected when their child count is implausible
       (`groupCountPlausible()`: more than 4096 children, or more children than
       the section declares layers). Ordinary record bytes can satisfy every
       other header check — sample `Livery_4212_20260606043310` has a
       32768-child header whose `blocks = 4096` matches `ceil(count/8)`, and the
       4 KB child-type bitmap it implies swallowed 110 real records.
    A rejected record or a bogus header costs far more than itself: 32-byte
    alignment is lost, the walker byte-steps through the following records and
    the section stops short of its declared layer count. Measured on the
    maintainer's save: 80 of 1511 liveries lost 1–26 % of their layers before
    the patches (25 796 layers total), 18 after patches 1–2 (181 layers), 8
    after patch 3 (32 layers — the save dump drops from 152 to 32; the 1511-file
    collection is unchanged by patch 3). Of the remainder, several differ only
    because uploaded-logo placements are counted with a weight (the walk reaches
    every section target; the tree simply has fewer items).
  - `assets/raster/decal_textures.bin` — the raster decal pack is now
    PRE-BUILT and shipped with releases (built from the maintainer's game
    files; ~67 MB raw). The viewer loads it directly from `assets/` and only
    falls back to building from the user's `Decals.zip` when it is missing
    (e.g. a source checkout without the binary asset).
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

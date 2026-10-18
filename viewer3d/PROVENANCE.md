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
  - `fls/src/gui/canvas/native_shape_renderer.cpp` — the vinyl atlas is
    rendered into a 4x-multisampled FBO of the same size and resolved with
    `blitFramebuffer(GL_NEAREST)` (`beginScenePass`/`endScenePass`). Shape
    edges used to be hard-rasterised, so canvas resolution alone decided how
    blocky the artwork looked. The MSAA target is freed after every rebuild
    (8192x4096 RGBA8 x4 = ~537 MB at the 4x canvas, ~134 MB at 2x); when it
    cannot be created the renderer falls back to drawing straight into the
    atlas. The incremental (scissor-preserving) pass also stays direct — an
    empty MSAA target would erase the sections it does not redraw.
    `FLS_VERIFY_ATLAS=1` samples 32 tiles of the atlas after each rebuild and
    prints coverage, which is how a bad resolve is caught without watching the
    screen; `FLS_ATLAS_MSAA=0` turns the path off for on-screen A/B testing.
    **MSAA is skipped when the canvas is wider than 4096 px** (i.e. the 4x
    texture-quality setting): at 8192x4096 one atlas texel is already ~1/8 of
    an on-screen car pixel and the atlas is sampled bilinearly with mipmaps, so
    the multisample pass has nothing left to smooth — measured over a
    1200x820 window with the project's sample liveries, canvas pixels differ by
    at most 12/255 on 0.0006 % of pixels with MSAA on vs off — while it costs
    ~442 MB of VRAM and ~510 MB of commit per viewer instance, which is what
    caps how many preview windows can be open at once. The 1x/2x settings keep
    MSAA, where the atlas resolution is close to the display resolution.
  - `fls/src/gui/widgets/car_preview_widget.cpp` — orbit drag sensitivity
    0.01 -> 0.005 rad/px (user feedback: default felt too twitchy); new
    `highestDetailLodIndex()` (max-triangle LOD) so the viewer defaults to
    the highest-detail model regardless of LOD ordering; `smoothLiveryTexture()`
    fixes hard stair-steps on every vinyl edge — the livery atlas is an FBO
    texture and Qt leaves its filters at `GL_NEAREST`, so the 8192x4096 canvas
    was sampled nearest-neighbour onto a ~1k-pixel car. Now bilinear
    magnification, mipmapped minification and anisotropic filtering (up to 16x
    where the driver allows it), regenerated only when the atlas changes. The
    widget framebuffer went from 4x to 8x MSAA (verified `GL_SAMPLES` = 8).
  - `fls/src/core/raster_decals.cpp` — additional cache lookup path under
    `QStandardPaths::AppDataLocation` (raster decal pack built on first run
    from the user's game files instead of shipping a 67 MB binary). The pack is
    also **memory-mapped instead of `readAll()`**: it is read-only and
    byte-identical in every viewer process, so mapping it lets Windows keep one
    physical copy shared between concurrently open preview windows (each window
    used to commit its own 67 MB copy). Raster decal bytes are consequently a
    non-owning `QByteArray` view — `RasterDecalPack::mapping_` owns the open
    `QFile` that keeps the mapping alive — and `load()` falls back to
    `readAll()` when `QFile::map()` fails.
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

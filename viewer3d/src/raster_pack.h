#pragma once

#include <QString>

namespace fls {

// Path of the raster decal pack built from the user's game files (cached
// under AppData; see raster_pack.cpp).
QString rasterDecalPackPath();

// Builds the FH6RAST1 decal pack from <gameRoot>/media/Livery/Decals.zip
// (Textures/decal*.swatchbin) if the cached pack is missing or stale.
// Returns true when a usable pack exists afterwards; on failure returns
// false with *error set (raster decals then render as missing textures).
bool ensureRasterDecalPack(const QString &gameRoot, QString *error = nullptr);

} // namespace fls

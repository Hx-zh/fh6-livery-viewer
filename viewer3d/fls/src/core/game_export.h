#pragma once

#include "core_types.h"
#include "nested_payload.h"

namespace fls {

enum class ExportGameVersion {
    Horizon6,
    Horizon5,
    Motorsport2023,
    Horizon2,
    Horizon3,
    Motorsport5,
    Motorsport6,
};

QString exportThumbnailName(ExportGameVersion game, bool livery);
QByteArray encodeGameLiveryPayload(const Project &project, ExportGameVersion game);
void exportGameLivery(const Project &project, const QString &outputFolder,
                      ExportGameVersion game);
void exportGameGroup(const Project &project, const QString &outputFolder,
                     ExportGameVersion game, const SpriteSizeFn &spriteSize = {});

} // namespace fls

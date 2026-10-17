#pragma once

#include "core_types.h"

#include <QByteArray>
#include <QSizeF>

#include <functional>

namespace fls {

using SpriteSizeFn = std::function<QSizeF(quint16)>;

enum class GroupRecordGeneration : quint8 {
    First = 1,
    Second = 2,
    Current = 3,
};

QByteArray buildNestedPayload(const Project &project, const SpriteSizeFn &spriteSize = {},
                             GroupRecordGeneration generation = GroupRecordGeneration::Current,
                             bool centerArtwork = true);

} // namespace fls

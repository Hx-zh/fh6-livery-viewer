#include "raster_decals.h"

#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QStandardPaths>

#include <algorithm>
#include <memory>

namespace fls {
namespace {

constexpr qsizetype HeaderSize = 16;
constexpr qsizetype EntrySize = 24;
constexpr char Magic[] = "FH6RAST1";

quint32 readU32(const QByteArray &data, qsizetype pos) {
    return quint32(quint8(data[pos]))
        | (quint32(quint8(data[pos + 1])) << 8)
        | (quint32(quint8(data[pos + 2])) << 16)
        | (quint32(quint8(data[pos + 3])) << 24);
}

void setError(QString *error, const QString &message) {
    if (error != nullptr) {
        *error = message;
    }
}

} // namespace

bool RasterDecalPack::load(const QString &path, QString *error) {
    // Reset first: a second load() that fails must not leave `data_` pointing at memory the
    // previous mapping owned (that mapping is dropped when `mapping_` is replaced) while
    // `loaded_` still claims success.
    loaded_ = false;
    mapping_.reset();
    data_.clear();
    entries_.clear();

    auto file = std::make_unique<QFile>(path);
    if (!file->open(QIODevice::ReadOnly)) {
        setError(error, QStringLiteral("could not open raster decal pack: %1").arg(path));
        return false;
    }

    // FH6LiveryViewer local change: memory-map the pack instead of reading it into a private
    // buffer. The pack is read-only and byte-identical for every viewer process, so mapping it
    // lets Windows keep one physical copy and share the pages between concurrently open preview
    // windows (each window used to commit its own 67 MB copy). Falls back to readAll() if the
    // mapping fails (e.g. a filesystem without mapping support).
    QByteArray bytes;
    const qint64 packSize = file->size();
    const uchar *mapped = packSize > 0 ? file->map(0, packSize) : nullptr;
    if (mapped != nullptr) {
        bytes = QByteArray::fromRawData(reinterpret_cast<const char *>(mapped),
                                        static_cast<int>(packSize));
        mapping_ = std::move(file);
    } else {
        bytes = file->readAll();
    }
    if (bytes.size() < HeaderSize || bytes.left(8) != QByteArray(Magic, 8)) {
        setError(error, QStringLiteral("not a raster decal pack: %1").arg(path));
        return false;
    }
    const quint32 version = readU32(bytes, 8);
    const quint32 count = readU32(bytes, 12);
    if (version != 1) {
        setError(error, QStringLiteral("unsupported raster decal pack version %1").arg(version));
        return false;
    }
    const qsizetype entriesEnd = HeaderSize + qsizetype(count) * EntrySize;
    if (entriesEnd < HeaderSize || entriesEnd > bytes.size()) {
        setError(error, QStringLiteral("truncated raster decal pack index: %1").arg(path));
        return false;
    }

    QHash<quint32, Entry> parsed;
    parsed.reserve(static_cast<int>(count));
    for (quint32 i = 0; i < count; ++i) {
        const qsizetype pos = HeaderSize + qsizetype(i) * EntrySize;
        Entry entry;
        entry.id = readU32(bytes, pos + 0);
        entry.width = readU32(bytes, pos + 4);
        entry.height = readU32(bytes, pos + 8);
        entry.offset = readU32(bytes, pos + 12);
        entry.size = readU32(bytes, pos + 16);
        entry.flags = readU32(bytes, pos + 20);
        const quint64 expectedSize = quint64(entry.width) * quint64(entry.height) * 4ULL;
        if (entry.id == 0 || entry.width == 0 || entry.height == 0
            || entry.flags != 0 || expectedSize != entry.size
            || quint64(entry.offset) + quint64(entry.size) > quint64(bytes.size())) {
            setError(error, QStringLiteral("invalid raster decal pack entry %1 in %2").arg(i).arg(path));
            return false;
        }
        parsed.insert(entry.id, entry);
    }

    data_ = std::move(bytes);
    entries_ = std::move(parsed);
    path_ = path;
    loaded_ = true;
    return true;
}

QVector<quint32> RasterDecalPack::ids() const {
    QVector<quint32> result;
    result.reserve(entries_.size());
    for (auto it = entries_.cbegin(); it != entries_.cend(); ++it) {
        result.push_back(it.key());
    }
    std::sort(result.begin(), result.end());
    return result;
}

QSize RasterDecalPack::decalSize(quint32 id) const {
    const auto it = entries_.constFind(id);
    if (it == entries_.constEnd()) {
        return {};
    }
    return QSize(static_cast<int>(it->width), static_cast<int>(it->height));
}

RasterDecal RasterDecalPack::decal(quint32 id) const {
    const auto it = entries_.constFind(id);
    if (it == entries_.constEnd()) {
        return {};
    }
    RasterDecal decal;
    decal.id = it->id;
    decal.width = static_cast<int>(it->width);
    decal.height = static_cast<int>(it->height);
    decal.rgba = data_.mid(static_cast<qsizetype>(it->offset), static_cast<qsizetype>(it->size));
    return decal;
}

const RasterDecalPack &sharedRasterDecals() {
    static RasterDecalPack pack = [] {
        RasterDecalPack loaded;
        const QString appPath = QCoreApplication::applicationDirPath();
        // FH6LiveryViewer local change: also look in AppData, where the viewer
        // builds the pack on first run from the user's game files.
        const QStringList candidates = {
            QDir(appPath).filePath(QStringLiteral("assets/raster/decal_textures.bin")),
            QDir(QDir::currentPath()).filePath(QStringLiteral("assets/raster/decal_textures.bin")),
            QDir(QStandardPaths::writableLocation(QStandardPaths::AppDataLocation))
                .filePath(QStringLiteral("raster/decal_textures.bin")),
        };
        for (const QString &path : candidates) {
            if (QFileInfo::exists(path) && loaded.load(path)) {
                break;
            }
        }
        return loaded;
    }();
    return pack;
}

} // namespace fls

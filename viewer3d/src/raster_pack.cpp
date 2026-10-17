#include "raster_pack.h"

#include "binary_io.h"
#include "swatchbin.h"
#include "zip_extract.h"

#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QRegularExpression>
#include <QStandardPaths>

#include <algorithm>

namespace fls {
namespace {

// Same FH6RAST1 layout as Forza Livery Studio's tools/pack_decals.
constexpr quint32 kEntrySize = 24;
constexpr quint32 kHeaderSize = 16;

void appendLeU32(QByteArray &out, quint32 v)
{
    detail::appendLeU32(out, v);
}

struct DecalImage {
    quint32 id = 0;
    quint32 width = 0;
    quint32 height = 0;
    QByteArray rgba;
};

QByteArray buildPack(const QVector<DecalImage> &images)
{
    const quint32 count = static_cast<quint32>(images.size());
    quint32 dataOffset = kHeaderSize + count * kEntrySize;

    QByteArray out;
    out.reserve(static_cast<int>(dataOffset));
    out.append("FH6RAST1", 8);
    appendLeU32(out, 1); // version
    appendLeU32(out, count);

    QVector<quint32> offsets;
    offsets.reserve(images.size());
    for (const DecalImage &image : images) {
        offsets.push_back(dataOffset);
        dataOffset += static_cast<quint32>(image.rgba.size());
    }
    for (int i = 0; i < images.size(); ++i) {
        const DecalImage &image = images[i];
        appendLeU32(out, image.id);
        appendLeU32(out, image.width);
        appendLeU32(out, image.height);
        appendLeU32(out, offsets[i]);
        appendLeU32(out, static_cast<quint32>(image.rgba.size()));
        appendLeU32(out, 0); // RGBA8, top-left origin
    }
    for (const DecalImage &image : images) {
        out.append(image.rgba);
    }
    return out;
}

} // namespace

QString rasterDecalPackPath()
{
    return QDir(QStandardPaths::writableLocation(
                    QStandardPaths::AppDataLocation))
        .filePath(QStringLiteral("raster/decal_textures.bin"));
}

bool ensureRasterDecalPack(const QString &gameRoot, QString *error)
{
    const QString zipPath =
        QDir(gameRoot).filePath(QStringLiteral("media/Livery/Decals.zip"));
    const QFileInfo zipInfo(zipPath);
    if (!zipInfo.isFile()) {
        // No game files at hand: keep whatever cache exists, never fail hard.
        if (error != nullptr) {
            *error = QStringLiteral("Decals.zip not found under the game folder");
        }
        return QFile::exists(rasterDecalPackPath());
    }

    const QString packPath = rasterDecalPackPath();
    const QFileInfo packInfo(packPath);
    if (packInfo.isFile()
        && packInfo.lastModified() >= zipInfo.lastModified()) {
        return true; // cache exists and is newer than the game archive
    }

    QStringList entries;
    {
        QString listError;
        entries = listZipEntries(zipPath, &listError);
        if (entries.isEmpty()) {
            if (error != nullptr) {
                *error = QStringLiteral("Decals.zip: %1").arg(listError);
            }
            return false;
        }
    }

    static const QRegularExpression re(
        QStringLiteral("^Textures/decal(\\d+)\\.swatchbin$"),
        QRegularExpression::CaseInsensitiveOption);
    QVector<std::pair<quint32, QString>> decals; // id -> entry path
    for (const QString &entry : entries) {
        const auto match = re.match(entry);
        if (match.hasMatch()) {
            bool ok = false;
            const uint id = match.captured(1).toUInt(&ok);
            if (ok) {
                decals.push_back({static_cast<quint32>(id), entry});
            }
        }
    }
    std::sort(decals.begin(), decals.end(),
              [](const auto &a, const auto &b) { return a.first < b.first; });
    if (decals.isEmpty()) {
        if (error != nullptr) {
            *error = QStringLiteral("Decals.zip has no decal*.swatchbin entries");
        }
        return false;
    }

    QVector<DecalImage> images;
    images.reserve(decals.size());
    for (const auto &[id, entry] : decals) {
        QString entryError;
        const QByteArray bytes = readZipEntry(zipPath, entry, &entryError);
        if (bytes.isEmpty()) {
            if (error != nullptr) {
                *error = QStringLiteral("%1: %2").arg(entry, entryError);
            }
            return false;
        }
        const SwatchImage image = decodeSwatchImage(bytes, &entryError);
        if (!image.valid()) {
            if (error != nullptr) {
                *error = QStringLiteral("%1: %2").arg(
                    entry, entryError.isEmpty()
                               ? QStringLiteral("invalid swatchbin")
                               : entryError);
            }
            return false;
        }
        DecalImage decal;
        decal.id = id;
        decal.width = static_cast<quint32>(image.width);
        decal.height = static_cast<quint32>(image.height);
        decal.rgba = QByteArray(reinterpret_cast<const char *>(image.rgba.data()),
                                static_cast<int>(image.rgba.size()));
        images.push_back(std::move(decal));
    }

    QDir().mkpath(QFileInfo(packPath).absolutePath());
    QFile output(packPath);
    if (!output.open(QIODevice::WriteOnly)) {
        if (error != nullptr) {
            *error = QStringLiteral("cannot write %1").arg(packPath);
        }
        return false;
    }
    const QByteArray pack = buildPack(images);
    if (output.write(pack) != pack.size()) {
        output.remove();
        if (error != nullptr) {
            *error = QStringLiteral("short write to %1").arg(packPath);
        }
        return false;
    }
    return true;
}

} // namespace fls

#include "car_locator.h"

#include "binary_io.h"
#include "zip_extract.h"

#include <QDir>
#include <QFileInfo>
#include <QFileInfoList>
#include <QHash>
#include <QSet>

namespace fls {
namespace {

struct CarbinIdentity {
    quint32 ordinal = 0;
    QString mediaName;
};

struct OrdinalScan {
    QHash<quint32, QString> matches;
    QSet<quint32> ambiguous;
};

bool hasBytes(const QByteArray &bytes, qsizetype offset, qsizetype count)
{
    return offset >= 0 && count >= 0 && offset <= bytes.size()
        && count <= bytes.size() - offset;
}

bool readCarbinIdentity(const QByteArray &bytes, CarbinIdentity *identity)
{
    qsizetype offset = 0;
    const auto need = [&bytes](qsizetype position, qsizetype count) {
        return hasBytes(bytes, position, count);
    };
    if (!need(offset, 2)) {
        return false;
    }
    const quint16 version = detail::readLeU16(bytes, static_cast<int>(offset));
    offset += 2;
    if (version >= 3) {
        if (!need(offset, 16)) {
            return false;
        }
        offset += 16;
    }
    if (version >= 5) {
        if (!need(offset, 1)) {
            return false;
        }
        ++offset;
    }
    if (!need(offset, 8)) {
        return false;
    }
    identity->ordinal = detail::readLeU32(bytes, static_cast<int>(offset));
    offset += 4;
    const qint32 mediaNameLength = static_cast<qint32>(
        detail::readLeU32(bytes, static_cast<int>(offset)));
    offset += 4;
    if (mediaNameLength < 0 || mediaNameLength > (1 << 20)
        || !need(offset, mediaNameLength)) {
        return false;
    }
    identity->mediaName = QString::fromLatin1(bytes.constData() + offset, mediaNameLength);
    return true;
}

QString ordinalCacheKey(const QString &carsFolder)
{
    QString key = QDir(carsFolder).canonicalPath();
    if (key.isEmpty()) {
        key = QDir(carsFolder).absolutePath();
    }
    key = QDir::cleanPath(key);
#ifdef Q_OS_WIN
    key = key.toLower();
#endif
    return key;
}

OrdinalScan scanCarOrdinals(const QString &carsFolder)
{
    OrdinalScan scan;
    const QDir dir(carsFolder);
    const QFileInfoList archives = dir.entryInfoList(
        QStringList{QStringLiteral("*.zip")}, QDir::Files, QDir::Name);
    for (const QFileInfo &archive : archives) {
        const QString code = archive.completeBaseName();
        const QByteArray carbin = readZipEntry(
            archive.absoluteFilePath(), QStringLiteral("%1.carbin").arg(code));
        CarbinIdentity identity;
        if (!readCarbinIdentity(carbin, &identity)) {
            continue;
        }
        if (scan.ambiguous.contains(identity.ordinal)) {
            continue;
        }
        const auto existing = scan.matches.constFind(identity.ordinal);
        if (existing == scan.matches.constEnd()) {
            scan.matches.insert(identity.ordinal, archive.absoluteFilePath());
        } else if (existing.value() != archive.absoluteFilePath()) {
            scan.matches.erase(existing);
            scan.ambiguous.insert(identity.ordinal);
        }
    }
    return scan;
}

} // namespace

QString findCarModelPath(const QString &carsFolder, const QString &modelName)
{
    QDir dir(carsFolder);
    if (modelName.isEmpty() || !dir.exists()) {
        return QString();
    }
    const QStringList exts = {QStringLiteral("carbin"), QStringLiteral("zip"),
                              QStringLiteral("modelbin")};

    for (const QString &ext : exts) {
        const QString candidate = dir.filePath(
            QStringLiteral("%1.%2").arg(modelName, ext));
        if (QFileInfo(candidate).isFile()) {
            return candidate;
        }
    }

    const auto modelInDir = [](const QString &dirPath) -> QString {
        QDir sub(dirPath);
        for (const QString &pattern : {QStringLiteral("*.carbin"),
                                       QStringLiteral("*.zip")}) {
            const QStringList matches = sub.entryList(QStringList{pattern},
                                                      QDir::Files);
            if (!matches.isEmpty()) {
                return sub.filePath(matches.first());
            }
        }
        return QString();
    };

    const QFileInfoList entries =
        dir.entryInfoList(QDir::Dirs | QDir::Files | QDir::NoDotAndDotDot);
    for (const QFileInfo &info : entries) {
        if (info.isFile()) {
            if (info.completeBaseName().compare(modelName,
                                                Qt::CaseInsensitive) == 0
                && exts.contains(info.suffix().toLower())) {
                return info.filePath();
            }
        } else if (info.isDir()
                   && info.fileName().compare(modelName,
                                              Qt::CaseInsensitive) == 0) {
            const QString found = modelInDir(info.filePath());
            if (!found.isEmpty()) {
                return found;
            }
        }
    }
    return QString();
}

QString findCarModelPathByOrdinal(const QString &carsFolder, int carId)
{
    if (carId <= 0 || !QDir(carsFolder).exists()) {
        return {};
    }
    static QHash<QString, OrdinalScan> cache;
    const QString key = ordinalCacheKey(carsFolder);
    auto scan = cache.find(key);
    if (scan == cache.end()) {
        scan = cache.insert(key, scanCarOrdinals(carsFolder));
    }
    const quint32 ordinal = static_cast<quint32>(carId);
    if (scan->ambiguous.contains(ordinal)) {
        return {};
    }
    return scan->matches.value(ordinal);
}

} // namespace fls

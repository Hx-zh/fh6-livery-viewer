#include "viewer_assets.h"

#include <QCoreApplication>
#include <QCryptographicHash>
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonParseError>
#include <QList>

namespace viewer_assets {
namespace {

struct PackageLocation {
    QString filePath;
    QString root;
};

QString normalizedRelativePath(QString path)
{
    path.replace(QLatin1Char('\\'), QLatin1Char('/'));
    path = QDir::cleanPath(path);
    if (path.isEmpty() || path == QLatin1String(".")
        || path.startsWith(QLatin1Char('/'))
        || path.startsWith(QLatin1String("../"))
        || path == QLatin1String("..") || path.contains(QLatin1Char(':'))
        || QDir::isAbsolutePath(path)) {
        return {};
    }
    return path;
}

PackageLocation packageLocation(const QString &relativePath)
{
    const QString appDir = QCoreApplication::applicationDirPath();
    const QString cwd = QDir::currentPath();
    const QList<QString> roots = {
        QDir(appDir).filePath(QStringLiteral("assets")),
        QDir(cwd).filePath(QStringLiteral("assets")),
        QDir(cwd).filePath(QStringLiteral("cpp-port/assets")),
    };

    for (const QString &root : roots) {
        const QString path = QDir(root).filePath(relativePath);
        if (QFileInfo(path).isFile()) {
            return {path, root};
        }
    }
    for (const QString &root : roots) {
        if (QFileInfo(QDir(root).filePath(QStringLiteral("data_manifest.json"))).isFile()) {
            return {QDir(root).filePath(relativePath), root};
        }
    }
    return {QDir(roots.front()).filePath(relativePath), roots.front()};
}

QJsonObject readManifest(const QString &path)
{
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        return {};
    }
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll(), &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        return {};
    }
    return document.object();
}

bool manifestUpdated(const QJsonObject &manifest, QDateTime *updated)
{
    if (manifest.value(QStringLiteral("schema")).toInt(-1) != 1
        || !manifest.value(QStringLiteral("files")).isArray()) {
        return false;
    }
    const QDateTime parsed = QDateTime::fromString(
        manifest.value(QStringLiteral("updated")).toString(), Qt::ISODate);
    if (!parsed.isValid()) {
        return false;
    }
    *updated = parsed;
    return true;
}

bool findFileEntry(const QJsonObject &manifest, const QString &relativePath,
                   QJsonObject *result)
{
    const QString sourcePath = QStringLiteral("viewer3d/assets/%1").arg(relativePath);
    const QString targetPath = QStringLiteral("viewer_assets/%1").arg(relativePath);
    const QString kind = relativePath.endsWith(QStringLiteral(".gz"), Qt::CaseInsensitive)
        ? QStringLiteral("json.gz")
        : QStringLiteral("json");
    bool found = false;
    const QJsonArray files = manifest.value(QStringLiteral("files")).toArray();
    for (const QJsonValue &value : files) {
        if (!value.isObject()) {
            continue;
        }
        const QJsonObject entry = value.toObject();
        if (entry.value(QStringLiteral("path")).toString() != sourcePath
            || entry.value(QStringLiteral("target")).toString() != targetPath) {
            continue;
        }
        if (found || entry.value(QStringLiteral("kind")).toString() != kind) {
            return false;
        }
        *result = entry;
        found = true;
    }
    return found;
}

bool validCachedFile(const QString &cacheRoot, const QJsonObject &entry)
{
    const qint64 expectedBytes = entry.value(QStringLiteral("bytes")).toInteger(-1);
    const QByteArray expectedHash =
        entry.value(QStringLiteral("sha256")).toString().toLatin1().toLower();
    if (expectedBytes < 0 || expectedHash.size() != 64) {
        return false;
    }

    QFile file(QDir(cacheRoot).filePath(
        entry.value(QStringLiteral("target")).toString()));
    if (!file.open(QIODevice::ReadOnly) || file.size() != expectedBytes) {
        return false;
    }
    QCryptographicHash hash(QCryptographicHash::Sha256);
    return hash.addData(&file)
        && hash.result().toHex().compare(expectedHash, Qt::CaseInsensitive) == 0;
}

} // namespace

QString resolve(const QString &relativePath)
{
    const QString normalized = normalizedRelativePath(relativePath);
    if (normalized.isEmpty()) {
        return {};
    }
    const PackageLocation package = packageLocation(normalized);
    const QString localAppData = qEnvironmentVariable("LOCALAPPDATA").trimmed();
    if (localAppData.isEmpty()) {
        return package.filePath;
    }

    const QJsonObject packageManifest = readManifest(
        QDir(package.root).filePath(QStringLiteral("data_manifest.json")));
    const QString cacheRoot = QDir(localAppData).filePath(QStringLiteral("FH6LiveryViewer"));
    const QJsonObject cacheManifest = readManifest(
        QDir(cacheRoot).filePath(QStringLiteral("data_manifest.json")));
    QDateTime packageUpdated;
    QDateTime cacheUpdated;
    if (!manifestUpdated(packageManifest, &packageUpdated)
        || !manifestUpdated(cacheManifest, &cacheUpdated)
        || cacheUpdated < packageUpdated) {
        return package.filePath;
    }

    QJsonObject entry;
    if (!findFileEntry(cacheManifest, normalized, &entry)
        || !validCachedFile(cacheRoot, entry)) {
        return package.filePath;
    }
    return QDir(cacheRoot).filePath(entry.value(QStringLiteral("target")).toString());
}

} // namespace viewer_assets

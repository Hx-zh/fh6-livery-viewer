#include "car_locator.h"

#include <QDir>
#include <QFileInfo>
#include <QFileInfoList>

namespace fls {

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

} // namespace fls

#pragma once

#include "game_export.h"
#include "wgs_save_reader.h"

#include <QMap>

namespace fls {

struct WgsExportTarget {
    QString descriptorPath;
    QString indexPath;
    QString containerName;
    WgsAsset asset;
    QMap<QString, QString> blobPaths;
    QByteArray creatorTag;
    QByteArray headerBytes;
    QByteArray fingerprint;
    ExportGameVersion game = ExportGameVersion::Horizon5;
    int carId = 0;
};

WgsExportTarget inspectWgsExportTarget(const QString &descriptorPath,
                                      ExportGameVersion game, bool livery, int carId = 0);
QString replaceWgsContainer(const WgsExportTarget &target,
                            const QMap<QString, QByteArray> &replacementBlobs);

} // namespace fls

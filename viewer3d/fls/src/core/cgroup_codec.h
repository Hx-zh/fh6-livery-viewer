#pragma once

#include <QByteArray>
#include <QString>

namespace fls {

QByteArray inflateContainer(const QByteArray &wrapped);
QByteArray inflateFirstContainer(const QByteArray &wrapped);
QByteArray readCGroupPayload(const QString &folderOrFile);
QByteArray deflateContainer(const QByteArray &payload);
void writeCGroupFile(const QString &path, const QByteArray &payload);

} // namespace fls

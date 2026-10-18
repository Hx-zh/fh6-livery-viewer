#pragma once

#include <QByteArray>
#include <QHash>
#include <QSize>
#include <QString>
#include <QVector>

#include <memory>

class QFile;

namespace fls {

struct RasterDecal {
    quint32 id = 0;
    int width = 0;
    int height = 0;
    QByteArray rgba;

    bool valid() const { return id != 0 && width > 0 && height > 0 && !rgba.isEmpty(); }
};

class RasterDecalPack {
public:
    bool load(const QString &path, QString *error = nullptr);
    bool isLoaded() const { return loaded_; }
    QString path() const { return path_; }
    QVector<quint32> ids() const;
    QSize decalSize(quint32 id) const;
    RasterDecal decal(quint32 id) const;

private:
    struct Entry {
        quint32 id = 0;
        quint32 width = 0;
        quint32 height = 0;
        quint32 offset = 0;
        quint32 size = 0;
        quint32 flags = 0;
    };

    // FH6LiveryViewer local change: `data_` is usually a non-owning view onto a read-only
    // memory mapping of the pack (`mapping_` owns the open file that keeps it alive).
    // Declared before `data_` so the mapping outlives the view at destruction time.
    std::unique_ptr<QFile> mapping_;
    QByteArray data_;
    QHash<quint32, Entry> entries_;
    QString path_;
    bool loaded_ = false;
};

const RasterDecalPack &sharedRasterDecals();

} // namespace fls

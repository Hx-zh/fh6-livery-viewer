#include "shape_name_store.h"

#include "viewer_assets.h"

#include <QtCore>

namespace gui {

bool ShapeNameStore::loadDefault(QString *error) {
    const QString path = viewer_assets::resolve(QStringLiteral("vector/shape_names.json"));
    if (QFile::exists(path)) {
        return loadFromFile(path, error);
    }
    if (error != nullptr) {
        *error = QStringLiteral("shape_names.json was not found");
    }
    return false;
}

bool ShapeNameStore::loadFromFile(const QString &path, QString *error) {
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        if (error != nullptr) {
            *error = QStringLiteral("could not open names asset: %1").arg(path);
        }
        return false;
    }

    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll(), &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        if (error != nullptr) {
            *error = QStringLiteral("invalid names asset: %1").arg(parseError.errorString());
        }
        return false;
    }

    const QJsonObject root = document.object();
    QHash<int, QString> parsed;
    for (auto it = root.begin(); it != root.end(); ++it) {
        bool ok = false;
        const int shapeId = it.key().toInt(&ok);
        if (!ok || !it.value().isObject()) {
            continue;
        }
        const QString name = it.value().toObject().value(QStringLiteral("name")).toString().trimmed();
        if (!name.isEmpty()) {
            parsed.insert(shapeId, name);
        }
    }

    names_ = parsed;
    return true;
}

QString ShapeNameStore::name(int shapeId) const {
    return names_.value(shapeId);
}

bool ShapeNameStore::contains(int shapeId) const {
    return names_.contains(shapeId);
}

} // namespace gui

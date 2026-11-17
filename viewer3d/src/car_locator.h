#pragma once

#include <QString>

namespace fls {

// Locates the car archive for a model code inside the game's media/Cars
// directory. Ported from Forza Livery Studio's MainWindow::findCarModelPath.
QString findCarModelPath(const QString &carsFolder, const QString &modelName);

// Resolves an unregistered save car id directly from the installed car archives.
QString findCarModelPathByOrdinal(const QString &carsFolder, int carId);

} // namespace fls

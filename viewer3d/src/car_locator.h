#pragma once

#include <QString>

namespace fls {

// Locates the car archive for a model code inside the game's media/Cars
// directory. Ported from Forza Livery Studio's MainWindow::findCarModelPath.
QString findCarModelPath(const QString &carsFolder, const QString &modelName);

} // namespace fls

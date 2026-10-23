#include "viewer_window.h"

#include "car_locator.h"
#include "car_preview_widget.h"
#include "car_registry.h"
#include "game_paths.h"
#include "project_codec.h"
#include "raster_pack.h"

#include <QFileDialog>
#include <QFrame>
#include <QHBoxLayout>
#include <QLabel>
#include <QPushButton>
#include <QStatusBar>
#include <QVBoxLayout>

#include <array>
#include <iterator>

namespace {

constexpr int kTextureScales[] = {1, 2, 4};

} // namespace

ViewerWindow::ViewerWindow(fls::Project project, const QString &gameRoot,
                           const QString &liveryPath, QWidget *parent)
    : QMainWindow(parent), project_(std::move(project)), gameRoot_(gameRoot),
      currentLiveryPath_(liveryPath)
{
    setWindowTitle(QStringLiteral("%1 — FH6 3D")
                       .arg(project_.name));
    resize(1100, 700);

    auto *central = new QFrame(this);
    auto *layout = new QVBoxLayout(central);
    layout->setContentsMargins(8, 6, 8, 6);
    layout->setSpacing(6);

    auto *topBar = new QFrame(central);
    auto *topLayout = new QHBoxLayout(topBar);
    topLayout->setContentsMargins(0, 0, 0, 0);
    titleLabel_ = new QLabel(topBar);
    titleLabel_->setStyleSheet(QStringLiteral("font-weight: bold;"));
    topLayout->addWidget(titleLabel_, 1);
    qualityButton_ = new QPushButton(topBar);
    qualityButton_->setToolTip(QStringLiteral("Livery texture scale"));
    topLayout->addWidget(qualityButton_);
    auto *lodButton = new QPushButton(QStringLiteral("LOD"), topBar);
    lodButton->setToolTip(QStringLiteral("Cycle model level of detail"));
    topLayout->addWidget(lodButton);
    // 套件选择直达: 展开侧栏并切到 PARTS 页(原需先点 "<" 再点 PARTS 两步)
    auto *partsButton = new QPushButton(QStringLiteral("Parts"), topBar);
    partsButton->setToolTip(
        QStringLiteral("Show body kit and exterior part selection"));
    topLayout->addWidget(partsButton);
    auto *shotButton = new QPushButton(QStringLiteral("PNG"), topBar);
    shotButton->setToolTip(QStringLiteral("Save a snapshot image"));
    topLayout->addWidget(shotButton);
    layout->addWidget(topBar);

    preview_ = new gui::CarPreviewWidget(central);
    layout->addWidget(preview_, 1);

    statusLabel_ = new QLabel(central);
    layout->addWidget(statusLabel_);
    setCentralWidget(central);
    statusBar()->hide();

    const int scale = kTextureScales[qualityIndex_];
    qualityButton_->setText(QStringLiteral("%1x").arg(scale));
    connect(qualityButton_, &QPushButton::clicked, this, [this] {
        qualityIndex_ = (qualityIndex_ + 1)
                        % static_cast<int>(std::size(kTextureScales));
        preview_->setLiveryTextureScale(kTextureScales[qualityIndex_]);
        qualityButton_->setText(
            QStringLiteral("%1x").arg(kTextureScales[qualityIndex_]));
    });
    connect(lodButton, &QPushButton::clicked, this,
            [this] { preview_->cycleLod(); });
    connect(partsButton, &QPushButton::clicked, this,
            [this] { preview_->showPartsPanel(); });
    connect(shotButton, &QPushButton::clicked, this, [this] {
        const QString path = QFileDialog::getSaveFileName(
            this, QStringLiteral("Save snapshot"),
            QStringLiteral("%1.png").arg(project_.name),
            QStringLiteral("PNG (*.png)"));
        if (path.isEmpty()) {
            return;
        }
        preview_->renderThumbnail(QSize(1600, 900)).save(path, "PNG");
    });

    applyProject();
}

void ViewerWindow::setStartupNote(const QString &note)
{
    if (note.isEmpty()) {
        return;
    }
    const QString current = statusLabel_->text();
    if (current == QStringLiteral("Loading car model…")) {
        statusLabel_->setText(note);               // 加载占位被首个结果替换
        return;
    }
    statusLabel_->setText(current.isEmpty()
                              ? note
                              : QStringLiteral("%1 | %2").arg(current, note));
}

bool ViewerWindow::openLivery(const QString &path, const QString &gameRoot,
                              QString *error)
{
    fls::Project project;
    try {
        project = fls::importCLivery(path);
    } catch (const std::exception &ex) {
        if (error != nullptr) {
            *error = QString::fromUtf8(ex.what());
        }
        return false;
    }

    project_ = std::move(project);
    const QString previousRoot = gameRoot_;
    currentLiveryPath_ = path;
    if (!gameRoot.isEmpty()) {
        gameRoot_ = gameRoot;   // caller normalizes to the folder that holds media/Cars
    }
    // Drop the previous design's model and livery masks before the new ones are installed: the
    // masks drive the atlas packing layout, so reusing the old car's masks would lay the new
    // design out against the old car for as long as the new model takes to load — and forever if
    // that load fails. clearModel() also releases the previous ZIP extraction directory.
    preview_->clearModel();
    setWindowTitle(QStringLiteral("%1 — FH6 3D").arg(project_.name));
    statusLabel_->clear();
    applyProject();
    // A second preview should not keep the previous design's model around.
    preview_->markLiveryDirtyImmediate();
    // The raster decal pack is built per game folder and only happens in main(), so a window that
    // started without a folder would otherwise keep missing its logos after one is supplied here.
    if (!gameRoot_.isEmpty() && gameRoot_ != previousRoot) {
        QString rasterError;
        fls::ensureRasterDecalPack(gameRoot_, &rasterError);
        if (!rasterError.isEmpty()) {
            setStartupNote(QStringLiteral("raster decals: %1").arg(rasterError));
        }
    }
    return true;
}

void ViewerWindow::applyProject()
{
    const gui::CarRegistry &registry = gui::sharedCarRegistry();
    const QString carName = registry.name(project_.carId);
    titleLabel_->setText(QStringLiteral("%1%2%3").arg(
        project_.name,
        carName.isEmpty() ? QString() : QStringLiteral("  ·  "),
        carName));

    if (!gameRoot_.isEmpty()) {
        preview_->setGameFolder(gameRoot_);
    }
    // Editor-only unwrap overlays cost ~4s per car; the viewer never shows them.
    preview_->setBuildUnwrapOverlays(false);
    preview_->setProject(&project_);
    preview_->setLiveryTextureScale(kTextureScales[qualityIndex_]);
    preview_->markLiveryDirty();

    if (project_.carId == 0) {
        // No car to put the design on: drop any previously loaded model rather than leaving the
        // old car on screen under a new livery.
        preview_->clearModel();
        statusLabel_->setText(
            QStringLiteral("Livery loaded (no car id — model not loaded)"));
        return;
    }
    const QString modelCode = registry.modelCode(project_.carId);
    if (modelCode.isEmpty()) {
        preview_->clearModel();
        statusLabel_->setText(
            QStringLiteral("Unknown car id %1 — model not loaded")
                .arg(project_.carId));
        return;
    }
    loadCarModel();
}

void ViewerWindow::loadCarModel()
{
    if (gameRoot_.isEmpty()) {
        preview_->clearModel();
        statusLabel_->setText(
            QStringLiteral("No game folder — livery shown without car model"));
        return;
    }
    const QString carsDir = fls::gameCarsDir(gameRoot_);
    const QString modelCode =
        gui::sharedCarRegistry().modelCode(project_.carId);
    const QString path = fls::findCarModelPath(carsDir, modelCode);
    if (path.isEmpty()) {
        preview_->clearModel();
        statusLabel_->setText(
            QStringLiteral("Car archive %1 not found under %2")
                .arg(modelCode, carsDir));
        return;
    }
    statusLabel_->setText(QStringLiteral("Loading car model…"));
    preview_->loadCarAsync(path, [this](bool ok, const QString &error) {
        setStartupNote(ok ? QStringLiteral("Ready")
                          : QStringLiteral("Car model failed: %1").arg(error));
        if (ok) {
            // Default to the highest-detail LOD (user preference).
            preview_->applyHighestDetailLod();
        }
    });
}

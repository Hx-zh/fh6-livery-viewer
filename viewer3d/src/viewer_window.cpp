#include "viewer_window.h"

#include "car_locator.h"
#include "car_preview_widget.h"
#include "car_registry.h"
#include "game_paths.h"

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
                           QWidget *parent)
    : QMainWindow(parent), project_(std::move(project)), gameRoot_(gameRoot)
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
    preview_->setProject(&project_);
    preview_->setLiveryTextureScale(kTextureScales[qualityIndex_]);
    preview_->markLiveryDirty();

    if (project_.carId == 0) {
        statusLabel_->setText(
            QStringLiteral("Livery loaded (no car id — model not loaded)"));
        return;
    }
    const QString modelCode = registry.modelCode(project_.carId);
    if (modelCode.isEmpty()) {
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
        statusLabel_->setText(
            QStringLiteral("No game folder — livery shown without car model"));
        return;
    }
    const QString carsDir = fls::gameCarsDir(gameRoot_);
    const QString modelCode =
        gui::sharedCarRegistry().modelCode(project_.carId);
    const QString path = fls::findCarModelPath(carsDir, modelCode);
    if (path.isEmpty()) {
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
            preview_->selectLod(preview_->highestDetailLodIndex());
        }
    });
}

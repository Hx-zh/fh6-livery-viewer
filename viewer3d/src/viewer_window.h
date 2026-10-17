#pragma once

#include "core_types.h"

#include <QMainWindow>

#include <memory>

class QLabel;
class QPushButton;

namespace gui {
class CarPreviewWidget;
}

// Minimal read-only window: title bar info + the 3D preview widget.
class ViewerWindow final : public QMainWindow {
    Q_OBJECT
public:
    // Takes ownership of the imported livery project; gameRoot may be empty
    // (no car model is loaded then, the livery itself still renders).
    ViewerWindow(fls::Project project, const QString &gameRoot,
                 QWidget *parent = nullptr);

private:
    void applyProject();
    void loadCarModel();

    fls::Project project_;
    QString gameRoot_;
    gui::CarPreviewWidget *preview_ = nullptr;
    QLabel *titleLabel_ = nullptr;
    QLabel *statusLabel_ = nullptr;
    QPushButton *qualityButton_ = nullptr;
    int qualityIndex_ = 2; // index into kTextureScales

public:
    // 启动期/加载期备注(光栅缓存构建结果等), 追加显示在状态行(不覆盖已有内容)。
    void setStartupNote(const QString &note);
};

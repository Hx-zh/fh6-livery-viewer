#include "viewer_window.h"
#include "raster_pack.h"

#include "livery_codec.h"
#include "project_codec.h"

#include <QApplication>
#include <QCommandLineOption>
#include <QCommandLineParser>
#include <QDir>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QMessageBox>
#include <QRegularExpression>
#include <QSettings>

#include <algorithm>

namespace {

QString detectGameFolder()
{
    // Steam libraries from libraryfolders.vdf, then common roots.
    QStringList roots;
    QFile vdf(QStringLiteral(
        "C:/Program Files (x86)/Steam/steamapps/libraryfolders.vdf"));
    if (vdf.open(QIODevice::ReadOnly | QIODevice::Text)) {
        const QString text = QString::fromUtf8(vdf.readAll());
        static const QRegularExpression re(
            QStringLiteral("\"path\"\\s+\"([^\"]+)\""));
        auto it = re.globalMatch(text);
        while (it.hasNext()) {
            roots.push_back(it.next().captured(1).replace(
                QStringLiteral("\\\\"), QStringLiteral("/")));
        }
    }
    roots.push_back(QStringLiteral("C:/Program Files (x86)/Steam"));
    for (char drive = 'C'; drive <= 'H'; ++drive) {
        roots.push_back(QStringLiteral("%1:/SteamLibrary").arg(drive));
    }
    const QString relative = QStringLiteral(
        "steamapps/common/ForzaHorizon6");
    for (const QString &root : roots) {
        const QString candidate = QDir(root).filePath(relative);
        if (QFileInfo(candidate).isDir()) {
            return candidate;
        }
    }
    return QString();
}

QString resolveGameFolder(bool *cancelled)
{
    QSettings settings;
    QString folder = settings.value(QStringLiteral("gameFolder")).toString();
    if (!folder.isEmpty() && QFileInfo(folder).isDir()) {
        return folder;
    }
    folder = detectGameFolder();
    if (!folder.isEmpty()) {
        settings.setValue(QStringLiteral("gameFolder"), folder);
        return folder;
    }
    folder = QFileDialog::getExistingDirectory(
        nullptr, QStringLiteral("Forza Horizon 6 game folder"));
    if (folder.isEmpty()) {
        *cancelled = true;
        return QString();
    }
    settings.setValue(QStringLiteral("gameFolder"), folder);
    return folder;
}

} // namespace

int main(int argc, char *argv[])
{
    QCoreApplication::setOrganizationName(QStringLiteral("ForzaTools"));
    QCoreApplication::setApplicationName(QStringLiteral("FH6Livery3DViewer"));
    QCoreApplication::setAttribute(Qt::AA_ShareOpenGLContexts);
    QApplication app(argc, argv);

    QCommandLineParser parser;
    parser.setApplicationDescription(
        QStringLiteral("FH6 livery 3D preview (read-only viewer)"));
    parser.addHelpOption();
    parser.addPositionalArgument(
        QStringLiteral("livery"),
        QStringLiteral("C_livery file or its livery folder"));
    QCommandLineOption gameOption(
        {QStringLiteral("g"), QStringLiteral("game")},
        QStringLiteral("Forza Horizon 6 install folder"),
        QStringLiteral("folder"));
    parser.addOption(gameOption);
    parser.process(app);

    const QStringList positional = parser.positionalArguments();
    QString liveryPath = positional.isEmpty() ? QString() : positional.first();
    if (liveryPath.isEmpty()) {
        liveryPath = QFileDialog::getOpenFileName(
            nullptr, QStringLiteral("Open C_livery"), QString(),
            QStringLiteral("C_livery (C_livery Livery_*.C_livery);;"
                           "All files (*)"));
        if (liveryPath.isEmpty()) {
            return 0;
        }
    }

    fls::Project project;
    try {
        project = fls::importCLivery(liveryPath);
    } catch (const std::exception &ex) {
        QMessageBox::critical(
            nullptr, QStringLiteral("FH6 3D"),
            QStringLiteral("Could not read the livery:\n%1")
                .arg(QString::fromUtf8(ex.what())));
        return 1;
    }

    bool gameCancelled = false;
    QString gameRoot = parser.value(gameOption);
    if (gameRoot.isEmpty()) {
        gameRoot = resolveGameFolder(&gameCancelled);
    }

    // Raster decals: build the on-disk pack from the user's game files on
    // first run (or after a game update); failures only degrade logos.
    QString rasterNote;
    if (!gameRoot.isEmpty()) {
        QString rasterError;
        fls::ensureRasterDecalPack(gameRoot, &rasterError);
        rasterNote = rasterError.isEmpty()
                         ? QStringLiteral("raster decals: ready")
                         : QStringLiteral("raster decals: %1").arg(rasterError);
    } else {
        rasterNote = QStringLiteral("raster decals: no game folder");
    }

    ViewerWindow window(std::move(project), gameRoot);
    window.setStartupNote(rasterNote);
    window.show();
    return QApplication::exec();
}

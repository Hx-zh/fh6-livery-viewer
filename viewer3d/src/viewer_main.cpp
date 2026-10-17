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

bool isGameRoot(const QString &path)
{
    const QDir dir(path);
    return QFileInfo(QDir(dir.filePath(QStringLiteral("media"))).filePath(
                         QStringLiteral("Cars"))).isDir()
           || QFileInfo(QDir(dir.filePath(QStringLiteral("Content")))
                            .filePath(QStringLiteral("media/Cars"))).isDir();
}

QStringList steamRoots()
{
    QStringList roots;
    auto add = [&roots](const QString &p) {
        if (!p.isEmpty() && QFileInfo(p).isDir() && !roots.contains(p)) {
            roots.push_back(p);
        }
    };
    QSettings cu(QStringLiteral("HKEY_CURRENT_USER\\Software\\Valve\\Steam"),
                 QSettings::NativeFormat);
    add(cu.value(QStringLiteral("SteamPath")).toString());
    QSettings lm(QStringLiteral(
                     "HKEY_LOCAL_MACHINE\\SOFTWARE\\WOW6432Node\\Valve\\Steam"),
                 QSettings::NativeFormat);
    add(lm.value(QStringLiteral("InstallPath")).toString());
    add(QStringLiteral("C:/Program Files (x86)/Steam"));
    for (char drive = 'C'; drive <= 'J'; ++drive) {
        add(QStringLiteral("%1:/SteamLibrary").arg(drive));
        add(QStringLiteral("%1:/Steam").arg(drive));
    }
    return roots;
}

QString detectGameFolder()
{
    // Steam: registry roots -> every library in libraryfolders.vdf -> common dirs.
    QStringList libs;
    for (const QString &root : steamRoots()) {
        if (!libs.contains(root)) {
            libs.push_back(root);
        }
        QFile vdf(QDir(root).filePath(
            QStringLiteral("steamapps/libraryfolders.vdf")));
        if (!vdf.open(QIODevice::ReadOnly | QIODevice::Text)) {
            continue;
        }
        const QString text = QString::fromUtf8(vdf.readAll());
        static const QRegularExpression re(
            QStringLiteral("\"path\"\\s+\"([^\"]+)\""));
        auto it = re.globalMatch(text);
        while (it.hasNext()) {
            QString lib = it.next().captured(1);
            lib.replace(QStringLiteral("\\\\"), QStringLiteral("/"));
            if (!libs.contains(lib)) {
                libs.push_back(lib);
            }
        }
    }
    for (const QString &lib : libs) {
        const QString candidate =
            QDir(lib).filePath(QStringLiteral("steamapps/common/ForzaHorizon6"));
        if (isGameRoot(candidate)) {
            return candidate;
        }
    }
    // Xbox / Store builds: scan <drive>:/XboxGames for *Forza*Horizon*6* with media.
    for (char drive = 'C'; drive <= 'J'; ++drive) {
        const QDir xbox(QStringLiteral("%1:/XboxGames").arg(drive));
        if (!xbox.exists()) {
            continue;
        }
        const auto entries =
            xbox.entryInfoList(QDir::Dirs | QDir::NoDotAndDotDot);
        for (const QFileInfo &entry : entries) {
            if (!entry.fileName().contains(QStringLiteral("Forza"),
                                           Qt::CaseInsensitive)
                || !entry.fileName().contains(QStringLiteral("6"))) {
                continue;
            }
            if (isGameRoot(entry.absoluteFilePath())) {
                return entry.absoluteFilePath();
            }
            const QString content =
                QDir(entry.absoluteFilePath()).filePath(QStringLiteral("Content"));
            if (isGameRoot(content)) {
                return content;
            }
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

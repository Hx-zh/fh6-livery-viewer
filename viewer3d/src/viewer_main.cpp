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
#include <QLocalServer>
#include <QLocalSocket>
#include <QMessageBox>
#include <QRegularExpression>
#include <QSettings>

#include <algorithm>

namespace {

// --- Single-instance hand-off -------------------------------------------------
// When the host app asks for one preview window (the default), a second "3D
// preview" must reuse the open window instead of spawning another process: the
// host passes FH6_VIEWER_REUSE=1, the new process forwards its livery path to
// the running instance over a local socket and exits, and that instance swaps
// the design (and the model when the car differs) in place.
constexpr int kForwardConnectMs = 400;
constexpr int kForwardWriteMs = 600;

QString reuseServerName()
{
    QString user = qEnvironmentVariable("USERNAME");
    if (user.isEmpty()) {
        user = QStringLiteral("default");
    }
    return QStringLiteral("FH6Livery3DViewer-%1").arg(user);
}

bool reuseRequested()
{
    return qEnvironmentVariable("FH6_VIEWER_REUSE") == QLatin1String("1");
}

// path\n game\n — small enough to arrive in one read, but the reader buffers
// until it has both lines anyway.
QByteArray makePayload(const QString &liveryPath, const QString &gameRoot)
{
    return liveryPath.toUtf8() + '\n' + gameRoot.toUtf8() + '\n';
}

bool forwardToRunningInstance(const QString &liveryPath, const QString &gameRoot)
{
    QLocalSocket socket;
    socket.connectToServer(reuseServerName());
    if (!socket.waitForConnected(kForwardConnectMs)) {
        return false;
    }
    socket.write(makePayload(liveryPath, gameRoot));
    socket.flush();
    socket.waitForBytesWritten(kForwardWriteMs);
    socket.disconnectFromServer();
    return true;
}

bool isGameRoot(const QString &path)
{
    const QDir dir(path);
    return QFileInfo(QDir(dir.filePath(QStringLiteral("media"))).filePath(
                         QStringLiteral("Cars"))).isDir()
           || QFileInfo(QDir(dir.filePath(QStringLiteral("Content")))
                            .filePath(QStringLiteral("media/Cars"))).isDir();
}

// Normalize to the directory that directly contains media/Cars: Store builds
// keep it under <root>/Content, flat installs under <root>/media.
QString normalizeGameRoot(const QString &path)
{
    if (path.isEmpty()) {
        return path;
    }
    const QDir dir(path);
    if (QFileInfo(QDir(dir.filePath(QStringLiteral("media")))
                      .filePath(QStringLiteral("Cars"))).isDir()) {
        return path;
    }
    const QString content = QDir(path).filePath(QStringLiteral("Content"));
    if (QFileInfo(QDir(content).filePath(QStringLiteral("media/Cars"))).isDir()) {
        return content;
    }
    return path;
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
    for (char drive = 'C'; drive <= 'Z'; ++drive) {
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
            return normalizeGameRoot(candidate);
        }
    }
    // Xbox / Store builds: scan <drive>:/XboxGames for *Forza*Horizon*6* with media.
    for (char drive = 'C'; drive <= 'Z'; ++drive) {
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
                return normalizeGameRoot(entry.absoluteFilePath());
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
    // Store installs pass the outer folder; point at the media-containing dir.
    gameRoot = normalizeGameRoot(gameRoot);

    // Reuse request: hand this preview to the window that is already open and
    // stop. Only done when the host asked for a single preview window.
    const bool reuse = reuseRequested();
    if (reuse && forwardToRunningInstance(liveryPath, gameRoot)) {
        return 0;
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

    ViewerWindow window(std::move(project), gameRoot, liveryPath);
    window.setStartupNote(rasterNote);
    window.show();

    // Serve further previews into this window when the host asked for reuse.
    QLocalServer *server = nullptr;
    if (reuse) {
        server = new QLocalServer(&app);
        QLocalServer::removeServer(reuseServerName());   // clear a stale pipe
        if (!server->listen(reuseServerName())) {
            delete server;                               // reuse simply unavailable
            server = nullptr;
        } else {
            QObject::connect(server, &QLocalServer::newConnection, &window,
                             [server, &window]() {
                while (QLocalSocket *socket = server->nextPendingConnection()) {
                    // Buffer until both lines are in: a socket can deliver the
                    // payload in more than one chunk.
                    socket->setProperty("payload", QByteArray());
                    QObject::connect(
                        socket, &QLocalSocket::readyRead, socket, [socket, &window]() {
                            QByteArray payload =
                                socket->property("payload").toByteArray() + socket->readAll();
                            socket->setProperty("payload", payload);
                            const int first = payload.indexOf('\n');
                            if (first < 0) {
                                return;                     // wait for the rest
                            }
                            const int second = payload.indexOf('\n', first + 1);
                            if (second < 0) {
                                return;
                            }
                            const QString path = QString::fromUtf8(
                                payload.left(first));
                            const QString root = QString::fromUtf8(
                                payload.mid(first + 1, second - first - 1));
                            if (!path.isEmpty()
                                && path != window.currentLiveryPath()) {
                                QString error;
                                if (!window.openLivery(path, root, &error)) {
                                    QMessageBox::critical(
                                        nullptr, QStringLiteral("FH6 3D"),
                                        QStringLiteral("Could not read the livery:\n%1")
                                            .arg(error));
                                }
                            }
                            window.raise();
                            window.activateWindow();
                            socket->disconnectFromServer();
                        });
                }
            });
        }
    }
    Q_UNUSED(server);
    return QApplication::exec();
}

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
#include <QLockFile>
#include <QMessageBox>
#include <QRegularExpression>
#include <QSettings>
#include <QThread>
#include <QTimer>

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
// The client waits for a one-byte acknowledgement from the running window. Without it the
// forward "succeeds" as soon as the bytes reach the pipe, which is exactly how a hand-off gets
// lost when the owner is still starting up and has not accepted the connection yet.
constexpr int kForwardAckMs = 1200;
// Retries so a click that arrives while the first window is still starting up waits for it
// instead of racing it into a second window.
constexpr int kForwardAttempts = 20;
constexpr int kForwardRetryMs = 250;
// Instance ownership lives in a lock file: QLocalServer::listen() is not exclusive on Windows
// (the named pipe is created with unlimited instances), so two processes can both listen on the
// same name. QLockFile also spots a stale lock left by a crashed owner.
constexpr int kInstanceLockStaleMs = 30000;
constexpr int kSocketIdleTimeoutMs = 3000;
constexpr int kMaxPayloadBytes = 64 * 1024;

QString instanceLockPath()
{
    return QDir(QDir::tempPath()).filePath(QStringLiteral("FH6Livery3DViewer.lock"));
}

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
    if (socket.write(makePayload(liveryPath, gameRoot)) < 0) {
        return false;
    }
    socket.flush();
    if (!socket.waitForBytesWritten(kForwardWriteMs)) {
        return false;
    }
    // Wait for the running window to confirm it took the preview. A write into the pipe only
    // proves the bytes were queued: the owner may not have reached its event loop yet, and
    // treating that as success is how a hand-off silently disappears.
    if (!socket.waitForReadyRead(kForwardAckMs)) {
        socket.abort();
        return false;
    }
    const bool acknowledged = !socket.read(1).isEmpty();
    socket.disconnectFromServer();
    return acknowledged;
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

    // Reuse request: hand this preview to the window that is already open **before** importing
    // the livery or resolving the game folder. Doing that work first would parse the file twice
    // (the server re-imports it), and a missing game folder would pop a directory dialog in a
    // process that is only going to exit.
    const bool reuse = reuseRequested();
    const QString requestedRoot = normalizeGameRoot(parser.value(gameOption));

    // Claim the instance *first* — before the expensive import. Ownership comes from a lock file
    // because QLocalServer::listen() is not exclusive on Windows (the named pipe is created with
    // unlimited instances, so two processes can both listen on the same name); the socket only
    // carries the payload. A stale lock left by a crashed owner is reclaimed by QLockFile.
    QLockFile instanceLock(instanceLockPath());
    instanceLock.setStaleLockTime(kInstanceLockStaleMs);
    bool ownsInstance = reuse && instanceLock.tryLock(0);
    QLocalServer server;
    bool serverReady = false;
    if (ownsInstance) {
        QLocalServer::removeServer(reuseServerName());   // no-op on Windows, needed on Unix
        serverReady = server.listen(reuseServerName());
        if (!serverReady) {
            ownsInstance = false;
            instanceLock.unlock();
        }
    }

    // Not the owner: hand this preview to whoever is, waiting for it to finish starting up
    // (the loser of a simultaneous launch would otherwise open a second window).
    if (reuse && !ownsInstance && !liveryPath.isEmpty()) {
        for (int attempt = 0; attempt < kForwardAttempts; ++attempt) {
            if (forwardToRunningInstance(liveryPath, requestedRoot)) {
                return 0;
            }
            QThread::msleep(kForwardRetryMs);
        }
        // The owner never answered — it may have died between claiming and listening. Reclaim the
        // instance if its lock is now stale; otherwise open a window without a server.
        if (instanceLock.tryLock(0)) {
            ownsInstance = true;
            QLocalServer::removeServer(reuseServerName());
            serverReady = server.listen(reuseServerName());
            if (!serverReady) {
                instanceLock.unlock();
            }
        }
    }

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
    if (reuse && serverReady) {
        // The listener is created before the (expensive) import, so a forwarded preview can
        // already be waiting by the time we get here. Its newConnection() emission happened while
        // nothing was connected to the signal, so drain whatever is pending once the handler is
        // in place — otherwise those hand-offs are silently lost.
        auto servePending = [&server, &window]() {
            while (QLocalSocket *socket = server.nextPendingConnection()) {
                // The server owns the socket; make sure it dies with this hand-off instead of
                // accumulating for the process lifetime.
                socket->setProperty("payload", QByteArray());
                // A client that never finishes writing (killed mid-send) must not pin a socket
                // forever.
                QTimer::singleShot(kSocketIdleTimeoutMs, socket, [socket]() {
                    if (socket->state() != QLocalSocket::UnconnectedState) {
                        socket->abort();
                        socket->deleteLater();
                    }
                });
                auto consume = [socket, &window]() {
                    // deleteLater() is already queued: ignore any late readyRead.
                    if (socket->property("handled").toBool()) {
                        return;
                    }
                    QByteArray payload = socket->property("payload").toByteArray()
                        + socket->readAll();
                    socket->setProperty("payload", payload);
                    if (payload.size() > kMaxPayloadBytes) {
                        socket->abort();          // not one of ours
                        return;
                    }
                    // Buffer until both lines are in: a socket can deliver the payload in more
                    // than one chunk.
                    const int first = payload.indexOf('\n');
                    if (first < 0) {
                        return;
                    }
                    const int second = payload.indexOf('\n', first + 1);
                    if (second < 0) {
                        return;
                    }
                    const QString path = QString::fromUtf8(payload.left(first));
                    const QString root = normalizeGameRoot(
                        QString::fromUtf8(payload.mid(first + 1, second - first - 1)));
                    if (!path.isEmpty() && path != window.currentLiveryPath()) {
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
                    // Acknowledge only after the design was installed: the forwarded process
                    // stays alive until this byte arrives, which keeps the hand-off from being
                    // dropped when the owner was still starting up.
                    socket->write("1", 1);
                    socket->flush();
                    socket->waitForBytesWritten(kForwardWriteMs);
                    socket->setProperty("handled", true);
                    socket->disconnectFromServer();
                    socket->deleteLater();
                };
                QObject::connect(socket, &QLocalSocket::readyRead, socket, consume);
                // A client that forwarded while this window was still importing has already put
                // its payload into the buffer, so readyRead will never fire for it. Drain what is
                // there right away.
                if (socket->bytesAvailable() > 0) {
                    consume();
                }
            }
        };
        QObject::connect(&server, &QLocalServer::newConnection, &window,
                         [&servePending]() { servePending(); });
        if (server.hasPendingConnections()) {
            servePending();
        }
    }
    return QApplication::exec();
}

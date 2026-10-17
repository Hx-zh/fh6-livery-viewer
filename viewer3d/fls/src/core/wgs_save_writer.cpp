#include "wgs_save_writer.h"

#include "binary_io.h"
#include "cgroup_codec.h"
#include "header_codec.h"

#include <QCryptographicHash>
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QLockFile>
#include <QSaveFile>
#include <QUuid>

#include <stdexcept>

namespace fls {
namespace {

constexpr quint32 kIndexVersion = 14;
constexpr quint32 kDescriptorVersion = 4;
constexpr quint32 kSupportedContainerState = 1;
constexpr int kDescriptorHeaderSize = 8;
constexpr int kDescriptorEntrySize = 160;
constexpr int kBlobNameSize = 128;
constexpr int kCurrentGuidOffset = 144;
constexpr int kGuidSize = 16;
constexpr qint64 kMaximumFileSize = 64 * 1024 * 1024;
constexpr quint32 kMaximumEntries = 1000000;
constexpr quint64 kWindowsEpochOffset = 116444736000000000;
constexpr quint64 kFileTimeTicksPerMillisecond = 10000;
constexpr int kEntrySizeOffset = 16;
constexpr int kLiveryCreatorTagOffset = 33;
constexpr char kBackupFolder[] = ".fls-wgs-backups";
constexpr char kLockName[] = ".fls-wgs-export.lock";

struct Snapshot {
    WgsExportTarget target;
    QMap<QString, QByteArray> files;
    QMap<QString, QDateTime> modifiedTimes;
    QByteArray index;
    QByteArray descriptor;
    int indexTimeOffset = 0;
    int entryTimeOffset = 0;
};

void require(bool condition, const QString &message) {
    if (!condition) {
        throw std::runtime_error(message.toStdString());
    }
}

QByteArray readFile(const QString &path) {
    QFile file(path);
    require(file.open(QIODevice::ReadOnly), QStringLiteral("Could not read %1").arg(path));
    require(file.size() >= 0 && file.size() <= kMaximumFileSize,
            QStringLiteral("WGS file exceeds the supported size: %1").arg(path));
    const QByteArray bytes = file.readAll();
    require(bytes.size() == file.size(), QStringLiteral("Incomplete read of %1").arg(path));

    return bytes;
}

QString readString(const QByteArray &bytes, int &offset) {
    const quint32 count = detail::readLeU32(bytes, offset);
    offset += 4;
    require(count <= static_cast<quint32>((bytes.size() - offset) / 2),
            QStringLiteral("WGS index string is truncated"));
    QString value;
    value.reserve(count);
    for (quint32 index = 0; index < count; ++index) {
        value.append(QChar(detail::readLeU16(bytes, offset)));
        offset += 2;
    }

    return value;
}

void skip(const QByteArray &bytes, int &offset, int count) {
    require(offset >= 0 && count <= bytes.size() - offset,
            QStringLiteral("WGS index is truncated"));
    offset += count;
}

QString guidName(const QByteArray &bytes) {
    require(bytes.size() == kGuidSize, QStringLiteral("WGS GUID is truncated"));
    const QUuid guid = QUuid::fromRfc4122(bytes);
    require(!guid.isNull(), QStringLiteral("WGS GUID is empty"));
    const QString prefix = QStringLiteral("%1%2%3")
        .arg(detail::readLeU32(bytes, 0), 8, 16, QLatin1Char('0'))
        .arg(detail::readLeU16(bytes, 4), 4, 16, QLatin1Char('0'))
        .arg(detail::readLeU16(bytes, 6), 4, 16, QLatin1Char('0'));

    return (prefix + QString::fromLatin1(bytes.mid(8).toHex())).toUpper();
}

QString checkedFile(const QString &directory, const QString &name) {
    const QDir parent(directory);
    const QFileInfo info(parent.filePath(name));
    require(info.isFile() && !info.isSymLink()
                && info.canonicalFilePath() == QDir(parent.canonicalPath()).filePath(name),
            QStringLiteral("WGS file is missing or resolves outside its container: %1").arg(info.filePath()));

    return info.absoluteFilePath();
}

void readIndex(Snapshot &snapshot, const QString &containerDirectory) {
    const QByteArray &bytes = snapshot.index;
    const QString folderName = QFileInfo(containerDirectory).fileName();
    const QString descriptorName = QFileInfo(snapshot.target.descriptorPath).fileName();
    int offset = 0;
    int matches = 0;

    require(detail::readLeU32(bytes, offset) == kIndexVersion,
            QStringLiteral("Unsupported WGS index version"));
    const quint32 count = detail::readLeU32(bytes, 4);
    require(count <= kMaximumEntries, QStringLiteral("Invalid WGS index container count"));
    offset = 12;
    const QString package = readString(bytes, offset);
    require(package.endsWith(QStringLiteral("!Forzahorizon5"), Qt::CaseInsensitive),
            QStringLiteral("The selected WGS profile is not Forza Horizon 5"));
    snapshot.indexTimeOffset = offset;
    skip(bytes, offset, 12);
    readString(bytes, offset);
    skip(bytes, offset, 8);
    for (quint32 index = 0; index < count; ++index) {
        const QString name = readString(bytes, offset);
        readString(bytes, offset);
        readString(bytes, offset);
        require(offset < bytes.size(), QStringLiteral("WGS descriptor generation is truncated"));
        const quint8 generation = static_cast<quint8>(bytes.at(offset++));
        const quint32 state = detail::readLeU32(bytes, offset);
        skip(bytes, offset, 4);
        const QString directory = guidName(bytes.mid(offset, kGuidSize));
        skip(bytes, offset, kGuidSize);
        if (directory.compare(folderName, Qt::CaseInsensitive) == 0) {
            require(state == kSupportedContainerState,
                    QStringLiteral("Unsupported WGS container state (%1). Select a synchronized existing slot.").arg(state));
            require(descriptorName == QStringLiteral("container.%1").arg(generation),
                    QStringLiteral("This descriptor is stale; select container.%1 from the same folder.").arg(generation));
            snapshot.target.containerName = name;
            snapshot.entryTimeOffset = offset;
            ++matches;
        }
        skip(bytes, offset, 24);
    }
    require(matches == 1, QStringLiteral("The container is not uniquely registered in containers.index"));
    require(offset == bytes.size(), QStringLiteral("Unsupported trailing WGS index data"));
}

void readDescriptor(Snapshot &snapshot, const QString &directory) {
    const QByteArray &bytes = snapshot.descriptor;
    require(detail::readLeU32(bytes, 0) == kDescriptorVersion,
            QStringLiteral("Unsupported WGS descriptor version"));
    const quint32 count = detail::readLeU32(bytes, 4);
    require(count > 0 && count <= kMaximumEntries
                && bytes.size() == kDescriptorHeaderSize + static_cast<qint64>(count) * kDescriptorEntrySize,
            QStringLiteral("Invalid WGS descriptor size"));
    for (quint32 index = 0; index < count; ++index) {
        const int offset = kDescriptorHeaderSize + static_cast<int>(index) * kDescriptorEntrySize;
        QString name;
        for (int character = 0; character < kBlobNameSize; character += 2) {
            const quint16 value = detail::readLeU16(bytes, offset + character);
            if (value == 0) {
                break;
            }
            name.append(QChar(value));
        }
        const QString key = name.toLower();
        const QString fileName = guidName(bytes.mid(offset + kCurrentGuidOffset, kGuidSize));
        const QString path = checkedFile(directory, fileName);
        require(!name.isEmpty() && !snapshot.target.blobPaths.contains(key)
                    && !snapshot.files.contains(path), QStringLiteral("Duplicate or empty WGS blob entry"));
        snapshot.target.blobPaths.insert(key, path);
        snapshot.files.insert(path, readFile(path));
    }
}

QByteArray inflatedPayload(const QByteArray &wrapped, bool livery) {
    require(wrapped.size() >= 8 && detail::readLeU32(wrapped, 0) == wrapped.size() - 8
                && detail::readLeU32(wrapped, 4) > 0
                && detail::readLeU32(wrapped, 4) <= kMaximumFileSize,
            QStringLiteral("Invalid FH5 payload wrapper"));
    const QByteArray raw = inflateContainer(wrapped);
    require(raw.startsWith(livery ? QByteArray("vlrc", 4) : QByteArray("gyvl", 4))
                && detail::readLeU32(raw, 4) == 1, QStringLiteral("The payload is not the selected FH5 asset type"));

    return raw;
}

QByteArray fingerprint(const QMap<QString, QByteArray> &files) {
    QCryptographicHash hash(QCryptographicHash::Sha256);
    for (auto file = files.cbegin(); file != files.cend(); ++file) {
        hash.addData(file.key().toUtf8());
        hash.addData(QCryptographicHash::hash(file.value(), QCryptographicHash::Sha256));
    }

    return hash.result();
}

Snapshot readSnapshot(const QString &descriptorPath, ExportGameVersion game, bool livery, int carId) {
    require(game == ExportGameVersion::Horizon5, QStringLiteral("WGS export currently supports FH5 only"));
    const QFileInfo descriptorInfo(descriptorPath);
    const QString directory = descriptorInfo.absolutePath();
    const QDir profile(QFileInfo(directory).absolutePath());
    Snapshot snapshot;

    require(QDir::isAbsolutePath(descriptorPath) && descriptorInfo.fileName().startsWith(QStringLiteral("container.")),
            QStringLiteral("Select an existing container.1 or container.N file using an absolute path"));
    require(QDir(directory).canonicalPath() == QDir(profile.canonicalPath()).filePath(QFileInfo(directory).fileName()),
            QStringLiteral("WGS container folder resolves outside its profile"));
    snapshot.target.descriptorPath = checkedFile(directory, descriptorInfo.fileName());
    snapshot.target.indexPath = checkedFile(profile.absolutePath(), QStringLiteral("containers.index"));
    snapshot.target.game = game;
    snapshot.index = readFile(snapshot.target.indexPath);
    snapshot.descriptor = readFile(snapshot.target.descriptorPath);
    readIndex(snapshot, directory);
    require(snapshot.target.containerName.startsWith(livery ? QStringLiteral("Livery_") : QStringLiteral("LayerGroup_")),
            QStringLiteral("Select a full %1 slot of the same type as the project.").arg(livery ? QStringLiteral("livery") : QStringLiteral("layer group")));
    readDescriptor(snapshot, directory);
    const QString payloadKey = livery ? QStringLiteral("c_livery") : QStringLiteral("c_group");
    require(snapshot.target.blobPaths.contains(payloadKey) && snapshot.target.blobPaths.contains(QStringLiteral("header"))
                && !snapshot.target.blobPaths.contains(livery ? QStringLiteral("c_group") : QStringLiteral("c_livery")),
            QStringLiteral("The selected container has incomplete or mismatched asset blobs"));
    const QByteArray raw = inflatedPayload(snapshot.files.value(snapshot.target.blobPaths.value(payloadKey)), livery);
    snapshot.target.carId = livery ? static_cast<int>(detail::readLeU32(raw, 16)) : 0;
    if (livery) {
        bool validCar = false;
        const int registeredCar = snapshot.target.containerName.section(QLatin1Char('_'), 1, 1).toInt(&validCar);
        require(validCar && registeredCar == snapshot.target.carId && snapshot.target.carId == carId,
                QStringLiteral("Select a WGS livery slot for the project's target car (%1).").arg(carId));
    }
    snapshot.target.headerBytes = snapshot.files.value(snapshot.target.blobPaths.value(QStringLiteral("header")));
    const HeaderMetadata header = parseHeader(snapshot.target.headerBytes);
    require(header.guid.size() == kGuidSize && (header.formatVersion == 4 || header.formatVersion == 6),
            QStringLiteral("Unsupported FH5 WGS asset header"));
    const QString ownerText = profile.dirName().section(QLatin1Char('_'), 0, 0);
    bool validOwner = false;
    const quint64 owner = ownerText.toULongLong(&validOwner, 16);
    require(validOwner && ownerText.size() == 16 && owner != 0,
            QStringLiteral("The WGS profile folder must retain its account identifier"));
    for (int byte = 0; byte < 8; ++byte) {
        snapshot.target.creatorTag.append(static_cast<char>((owner >> (byte * 8)) & 0xff));
    }
    quint64 size = 0;
    for (const QByteArray &file : snapshot.files) {
        size += static_cast<quint64>(file.size());
    }
    const int sizeOffset = snapshot.entryTimeOffset + kEntrySizeOffset;
    const quint64 recordedSize = detail::readLeU32(snapshot.index, sizeOffset)
        | (static_cast<quint64>(detail::readLeU32(snapshot.index, sizeOffset + 4)) << 32);
    require(size == recordedSize, QStringLiteral("WGS size metadata has changed or is inconsistent"));
    snapshot.target.asset = {snapshot.target.containerName, snapshot.target.blobPaths.value(payloadKey),
        snapshot.target.blobPaths.value(QStringLiteral("header")),
        snapshot.target.blobPaths.value(livery ? QStringLiteral("bigthumb.png") : QStringLiteral("thumb.png")),
        livery ? WgsAssetKind::Livery : WgsAssetKind::Group};
    snapshot.files.insert(snapshot.target.descriptorPath, snapshot.descriptor);
    snapshot.files.insert(snapshot.target.indexPath, snapshot.index);
    for (auto file = snapshot.files.cbegin(); file != snapshot.files.cend(); ++file) {
        snapshot.modifiedTimes.insert(file.key(), QFileInfo(file.key()).lastModified());
    }
    snapshot.target.fingerprint = fingerprint(snapshot.files);

    return snapshot;
}

void writeFile(const QString &path, const QByteArray &bytes) {
    QSaveFile file(path);
    file.setDirectWriteFallback(false);
    require(file.open(QIODevice::WriteOnly) && file.write(bytes) == bytes.size() && file.commit(),
            QStringLiteral("Could not atomically write %1").arg(path));
}

void writeU64(QByteArray &bytes, int offset, quint64 value) {
    require(offset >= 0 && offset + 8 <= bytes.size(), QStringLiteral("Invalid WGS metadata offset"));
    for (int byte = 0; byte < 8; ++byte) {
        bytes[offset + byte] = static_cast<char>((value >> (byte * 8)) & 0xff);
    }
}

QMap<QString, QByteArray> prepareWrites(const Snapshot &snapshot, const QMap<QString, QByteArray> &blobs) {
    QMap<QString, QByteArray> writes;
    const bool livery = snapshot.target.asset.kind == WgsAssetKind::Livery;
    const QString payloadKey = livery ? QStringLiteral("c_livery") : QStringLiteral("c_group");

    require(blobs.contains(payloadKey) && blobs.contains(QStringLiteral("header")),
            QStringLiteral("WGS export requires an encoded payload and header"));
    for (auto blob = blobs.cbegin(); blob != blobs.cend(); ++blob) {
        require(snapshot.target.blobPaths.contains(blob.key()) && !blob.value().isEmpty()
                    && blob.value().size() <= kMaximumFileSize,
                QStringLiteral("Unregistered or invalid replacement blob: %1").arg(blob.key()));
        if (blob.key() != payloadKey && blob.key() != QStringLiteral("header")) {
            require((blob.key() == QStringLiteral("thumb.png") || blob.key() == QStringLiteral("bigthumb.png")
                        || blob.key() == QStringLiteral("smallthumb.png"))
                        && blob.value().startsWith(QByteArray::fromHex("89504e470d0a1a0a")),
                    QStringLiteral("Replacement thumbnails must be PNG blobs"));
        }
        writes.insert(snapshot.target.blobPaths.value(blob.key()), blob.value());
    }
    const QByteArray raw = inflatedPayload(blobs.value(payloadKey), livery);
    require(!livery || detail::readLeU32(raw, 16) == static_cast<quint32>(snapshot.target.carId),
            QStringLiteral("Replacement payload targets a different car"));
    require(!livery || (detail::readLeU32(raw, 8) == 0
                && raw.mid(kLiveryCreatorTagOffset, snapshot.target.creatorTag.size()) == snapshot.target.creatorTag),
            QStringLiteral("Replacement payload must be editable and assigned to the destination profile"));
    const HeaderMetadata oldHeader = parseHeader(snapshot.target.headerBytes);
    const HeaderMetadata header = parseHeader(blobs.value(QStringLiteral("header")));
    require(header.formatVersion == 4 && header.guid == oldHeader.guid && header.creatorTag == snapshot.target.creatorTag
                && (!livery || header.carId == static_cast<quint32>(snapshot.target.carId)),
            QStringLiteral("Replacement header must retain the WGS slot identity and profile owner"));
    quint64 size = 0;
    for (auto blob = snapshot.target.blobPaths.cbegin(); blob != snapshot.target.blobPaths.cend(); ++blob) {
        size += static_cast<quint64>(writes.contains(blob.value())
            ? writes.value(blob.value()).size() : snapshot.files.value(blob.value()).size());
    }
    QByteArray index = snapshot.index;
    const quint64 timestamp = kWindowsEpochOffset
        + static_cast<quint64>(QDateTime::currentDateTimeUtc().toMSecsSinceEpoch()) * kFileTimeTicksPerMillisecond;
    writeU64(index, snapshot.indexTimeOffset, timestamp);
    writeU64(index, snapshot.entryTimeOffset, timestamp);
    writeU64(index, snapshot.entryTimeOffset + kEntrySizeOffset, size);
    writes.insert(snapshot.target.indexPath, index);
    writes.insert(snapshot.target.descriptorPath, snapshot.descriptor);

    return writes;
}

QString createBackup(const Snapshot &snapshot) {
    const QDir profile(QFileInfo(snapshot.target.indexPath).absolutePath());
    const QString backupRoot = profile.filePath(QLatin1String(kBackupFolder));
    require(QDir().mkpath(backupRoot) && QDir(backupRoot).canonicalPath() == profile.filePath(QLatin1String(kBackupFolder)),
            QStringLiteral("Could not create the WGS backup folder within the profile"));
    const QString name = QDateTime::currentDateTimeUtc().toString(QStringLiteral("yyyyMMdd-HHmmss-zzz"))
        + QLatin1Char('-') + QUuid::createUuid().toString(QUuid::WithoutBraces);
    const QDir backup(QDir(backupRoot).filePath(name));
    for (auto file = snapshot.files.cbegin(); file != snapshot.files.cend(); ++file) {
        const QString path = backup.filePath(profile.relativeFilePath(file.key()));
        require(QDir().mkpath(QFileInfo(path).absolutePath()), QStringLiteral("Could not create WGS backup directories"));
        writeFile(path, file.value());
        QFile copy(path);
        require(copy.open(QIODevice::ReadWrite)
                    && copy.setFileTime(snapshot.modifiedTimes.value(file.key()), QFileDevice::FileModificationTime),
                QStringLiteral("Could not preserve a WGS backup timestamp"));
    }

    return backup.absolutePath();
}

void commitWrites(const Snapshot &snapshot, const QMap<QString, QByteArray> &writes, const QString &backup) {
    QStringList order = writes.keys();
    QStringList committed;
    order.removeAll(snapshot.target.descriptorPath);
    order.removeAll(snapshot.target.indexPath);
    order.append(snapshot.target.descriptorPath);
    order.append(snapshot.target.indexPath);
    try {
        for (const QString &path : order) {
            require(readFile(path) == snapshot.files.value(path), QStringLiteral("WGS save changed during export; close the game and retry"));
            writeFile(path, writes.value(path));
            committed.append(path);
        }
    } catch (const std::exception &error) {
        QStringList failed;
        for (auto path = committed.crbegin(); path != committed.crend(); ++path) {
            try {
                require(readFile(*path) == writes.value(*path),
                        QStringLiteral("A committed WGS file changed before rollback"));
                writeFile(*path, snapshot.files.value(*path));
                QFile file(*path);
                require(file.open(QIODevice::ReadWrite)
                            && file.setFileTime(snapshot.modifiedTimes.value(*path), QFileDevice::FileModificationTime),
                        QStringLiteral("Could not restore a WGS file timestamp"));
            } catch (const std::exception &) {
                failed.append(*path);
            }
        }
        throw std::runtime_error(QStringLiteral("%1\nBackup: %2%3")
            .arg(QString::fromUtf8(error.what()), backup,
                 failed.isEmpty() ? QStringLiteral("\nCompleted writes were rolled back.")
                                  : QStringLiteral("\nRestore these files from backup: %1").arg(failed.join(QLatin1Char('\n'))))
            .toStdString());
    }
}

} // namespace

WgsExportTarget inspectWgsExportTarget(const QString &descriptorPath, ExportGameVersion game, bool livery, int carId) {
    return readSnapshot(descriptorPath, game, livery, carId).target;
}

QString replaceWgsContainer(const WgsExportTarget &target, const QMap<QString, QByteArray> &replacementBlobs) {
    const QDir profile(QFileInfo(target.indexPath).absolutePath());
    QLockFile lock(profile.filePath(QLatin1String(kLockName)));
    require(lock.tryLock(0), QStringLiteral("Another WGS export is using this save profile"));
    const Snapshot snapshot = readSnapshot(target.descriptorPath, target.game,
                                          target.asset.kind == WgsAssetKind::Livery, target.carId);
    require(snapshot.target.fingerprint == target.fingerprint && snapshot.target.indexPath == target.indexPath,
            QStringLiteral("The selected WGS save changed. Reselect its descriptor before exporting."));
    const QMap<QString, QByteArray> writes = prepareWrites(snapshot, replacementBlobs);
    const QString backup = createBackup(snapshot);
    commitWrites(snapshot, writes, backup);

    return backup;
}

} // namespace fls

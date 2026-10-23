#include "viewer_i18n.h"

#include "gui_assets.h"

#include <QFile>
#include <QHash>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonValue>

namespace viewer_i18n {
namespace {

QString g_language;

QString normalizeLanguage(const QString &raw)
{
    // Accept the app's dev values (zh / zhtw / en / ja / ko) and the game's own language names.
    const QString code = raw.trimmed().toLower().replace(QLatin1Char('_'), QLatin1Char('-'));
    if (code.isEmpty()) {
        return {};
    }
    if (code == QLatin1String("zh") || code == QLatin1String("zh-cn")
        || code == QLatin1String("zh-hans") || code == QLatin1String("chs")
        || code == QLatin1String("cn")) {
        return QStringLiteral("zh-cn");
    }
    if (code == QLatin1String("zhtw") || code == QLatin1String("zh-tw")
        || code == QLatin1String("zh-hk") || code == QLatin1String("zh-hant")
        || code == QLatin1String("cht")) {
        return QStringLiteral("zh-tw");
    }
    if (code == QLatin1String("en") || code.startsWith(QLatin1String("en-"))) {
        return QStringLiteral("en");
    }
    if (code == QLatin1String("ja") || code == QLatin1String("jp")
        || code.startsWith(QLatin1String("ja-"))) {
        return QStringLiteral("ja");
    }
    if (code == QLatin1String("ko") || code == QLatin1String("kr")
        || code.startsWith(QLatin1String("ko-"))) {
        return QStringLiteral("ko");
    }
    return {};
}

struct Catalog {
    QHash<QString, QHash<QString, QString>> names;   // model code -> "partType:optionId" -> name
    QHash<int, QString> partTypes;                   // partType id -> category name
};

QString catalogFile(const QString &code)
{
    // The base file stays English: it is the upstream table the host ships as-is.
    if (code == QLatin1String("en")) {
        return QStringLiteral("cars/part_names.json");
    }
    return QStringLiteral("cars/part_names.%1.json").arg(code);
}

Catalog loadCatalog(const QString &code)
{
    Catalog catalog;
    QFile file(gui::assetPath(catalogFile(code)));
    if (!file.open(QIODevice::ReadOnly)) {
        return catalog;
    }
    const QJsonDocument doc = QJsonDocument::fromJson(file.readAll());
    if (!doc.isObject()) {
        return catalog;
    }
    const QJsonObject root = doc.object();
    for (auto it = root.constBegin(); it != root.constEnd(); ++it) {
        // "_partTypes" / "_lang" / "_source" / "_updated" are generation metadata.
        if (it.key().startsWith(QLatin1Char('_'))) {
            if (it.key() == QLatin1String("_partTypes") && it.value().isObject()) {
                const QJsonObject types = it.value().toObject();
                for (auto type = types.constBegin(); type != types.constEnd(); ++type) {
                    bool ok = false;
                    const int id = type.key().toInt(&ok);
                    if (ok && type.value().isString()) {
                        catalog.partTypes.insert(id, type.value().toString());
                    }
                }
            }
            continue;
        }
        if (!it.value().isObject()) {
            continue;
        }
        const QJsonObject entries = it.value().toObject();
        QHash<QString, QString> names;
        for (auto entry = entries.constBegin(); entry != entries.constEnd(); ++entry) {
            if (entry.value().isString()) {
                names.insert(entry.key(), entry.value().toString());
            }
        }
        catalog.names.insert(it.key().toLower(), names);
    }
    return catalog;
}

const Catalog &catalogFor(const QString &code)
{
    static QHash<QString, Catalog> cache;
    const auto cached = cache.constFind(code);
    if (cached != cache.constEnd()) {
        return cached.value();
    }
    return cache.insert(code, loadCatalog(code)).value();
}

QString nameFrom(const Catalog &catalog, const QString &model, const QString &key)
{
    const auto modelIt = catalog.names.constFind(model);
    if (modelIt == catalog.names.constEnd()) {
        return {};
    }
    return modelIt->value(key);
}

} // namespace

QString languageCode()
{
    if (g_language.isEmpty()) {
        const QString fromEnv = normalizeLanguage(qEnvironmentVariable("FH6_LANG"));
        g_language = fromEnv.isEmpty() ? QStringLiteral("en") : fromEnv;
    }
    return g_language;
}

void setLanguageCode(const QString &raw)
{
    const QString code = normalizeLanguage(raw);
    g_language = code.isEmpty() ? QStringLiteral("en") : code;
}

bool isCjk()
{
    const QString code = languageCode();
    return code == QLatin1String("zh-cn") || code == QLatin1String("zh-tw")
        || code == QLatin1String("ja") || code == QLatin1String("ko");
}

QString partName(const QString &modelCode, int partType, int optionId)
{
    const QString model = modelCode.toLower();
    const QString key = QStringLiteral("%1:%2").arg(partType).arg(optionId);
    const QString name = nameFrom(catalogFor(languageCode()), model, key);
    if (!name.isEmpty() || languageCode() == QLatin1String("en")) {
        return name;
    }
    // A localized catalog that is missing this option (or the whole file) still shows the
    // English name instead of nothing.
    return nameFrom(catalogFor(QStringLiteral("en")), model, key);
}

QString partTypeName(int partType)
{
    const Catalog &catalog = catalogFor(languageCode());
    const auto it = catalog.partTypes.constFind(partType);
    return it == catalog.partTypes.constEnd() ? QString() : it.value();
}

// Chrome string tables: English source text -> localized text, one table per language. The
// terminal order follows the host app's language list (zh / zhtw / en / ja / ko).
static const QHash<QString, QString> &chromeTable(const QString &code)
{
    static const QHash<QString, QString> zhCn{
        // Top bar, dialogs and status messages (viewer_window.cpp / viewer_main.cpp)
        {QStringLiteral("Livery texture scale"), QStringLiteral("涂装纹理精度")},
        {QStringLiteral("Cycle model level of detail"), QStringLiteral("切换模型细节层级")},
        {QStringLiteral("Parts"), QStringLiteral("套件")},
        {QStringLiteral("Show body kit and exterior part selection"),
         QStringLiteral("显示车身套件与外观部件选择")},
        {QStringLiteral("Save a snapshot image"), QStringLiteral("保存快照图片")},
        {QStringLiteral("Save snapshot"), QStringLiteral("保存快照")},
        {QStringLiteral("Loading car model…"), QStringLiteral("正在加载车模…")},
        {QStringLiteral("Ready"), QStringLiteral("就绪")},
        {QStringLiteral("Car model failed: %1"), QStringLiteral("车模加载失败: %1")},
        {QStringLiteral("Livery loaded (no car id — model not loaded)"),
         QStringLiteral("涂装已加载（无车型 ID，未加载车模）")},
        {QStringLiteral("Unknown car id %1 — model not loaded"),
         QStringLiteral("未知车型 ID %1（未加载车模）")},
        {QStringLiteral("No game folder — livery shown without car model"),
         QStringLiteral("无游戏目录 — 仅显示涂装，不加载车模")},
        {QStringLiteral("Car archive %1 not found under %2"),
         QStringLiteral("在 %2 下未找到车模包 %1")},
        {QStringLiteral("raster decals: %1"), QStringLiteral("贴花图集: %1")},
        {QStringLiteral("raster decals: ready"), QStringLiteral("贴花图集: 就绪")},
        {QStringLiteral("raster decals: no game folder"), QStringLiteral("贴花图集: 无游戏目录")},
        {QStringLiteral("Forza Horizon 6 game folder"),
         QStringLiteral("选择《极限竞速：地平线 6》安装目录")},
        {QStringLiteral("Open C_livery"), QStringLiteral("打开 C_livery")},
        {QStringLiteral("C_livery (C_livery Livery_*.C_livery);;All files (*)"),
         QStringLiteral("C_livery (C_livery Livery_*.C_livery);;所有文件 (*)")},
        {QStringLiteral("Could not read the livery:\n%1"),
         QStringLiteral("无法读取涂装:\n%1")},
        {QStringLiteral("This is not a Forza Horizon 6 install folder (it must contain "
                        "media/Cars and must not be another Forza generation).\n\n"
                        "Please choose again, or cancel."),
         QStringLiteral("这不是《极限竞速：地平线 6》的安装目录（需要包含 media/Cars，"
                        "且不能是 Forza Horizon 5 等其它代数）。\n\n请重新选择，或取消。")},
        // Raster decal pack errors (raster_pack.cpp, shown after "贴花图集: ")
        {QStringLiteral("Decals.zip not found under the game folder"),
         QStringLiteral("游戏目录下未找到 Decals.zip")},
        {QStringLiteral("Decals.zip has no decal*.swatchbin entries"),
         QStringLiteral("Decals.zip 中没有 decal*.swatchbin 条目")},
        {QStringLiteral("invalid swatchbin"), QStringLiteral("无效的 swatchbin")},
        {QStringLiteral("cannot write %1"), QStringLiteral("无法写入 %1")},
        {QStringLiteral("short write to %1"), QStringLiteral("写入 %1 不完整")},
        {QStringLiteral("cannot replace %1"), QStringLiteral("无法替换 %1")},
        // Car model load errors (car_preview_widget.cpp, shown after "车模加载失败: ")
        {QStringLiteral("cannot create temporary directory for %1"),
         QStringLiteral("无法创建临时目录: %1")},
        {QStringLiteral("zip: no .carbin found in %1"),
         QStringLiteral("压缩包 %1 中没有 .carbin")},
        // Side panel (car_preview_widget.cpp)
        {QStringLiteral("PARTS"), QStringLiteral("套件")},
        {QStringLiteral("Only for reference, ingame render may differ"),
         QStringLiteral("仅供参考，游戏内实际渲染可能不同")},
        {QStringLiteral("No suitable graphics API"), QStringLiteral("无可用图形 API")},
        {QStringLiteral("Loading model"), QStringLiteral("正在加载车模")},
        {QStringLiteral("No model found"), QStringLiteral("未找到车模")},
        {QStringLiteral("Hide preview controls"), QStringLiteral("隐藏预览控制")},
        {QStringLiteral("Show preview controls"), QStringLiteral("显示预览控制")},
        {QStringLiteral("Level of detail"), QStringLiteral("细节层级")},
        {QStringLiteral("Select which level of detail is rendered in the preview."),
         QStringLiteral("选择预览中渲染的模型细节层级。")},
        {QStringLiteral("Load a car to choose its model detail."),
         QStringLiteral("加载车模后可选择其细节层级。")},
        {QStringLiteral("This car only provides one detail level."),
         QStringLiteral("该车型只有一个细节层级。")},
        {QStringLiteral("Body parts"), QStringLiteral("车身套件")},
        {QStringLiteral("Choose the body kit and installed exterior parts."),
         QStringLiteral("选择车身套件与已安装的外观部件。")},
        {QStringLiteral("This car has no selectable exterior part variants."),
         QStringLiteral("该车型没有可选的外观部件。")},
        {QStringLiteral("Load a car to see its available parts."),
         QStringLiteral("加载车模后可查看可用部件。")},
        // Generic part labels used when the catalog has no entry
        {QStringLiteral("Part"), QStringLiteral("部件")},
        {QStringLiteral("None (Stock)"), QStringLiteral("无（原厂）")},
        {QStringLiteral("Stock"), QStringLiteral("原厂")},
        {QStringLiteral("Body Kit"), QStringLiteral("车身套件")},
        {QStringLiteral("Body Kit %1"), QStringLiteral("车身套件 %1")},
        {QStringLiteral("Race"), QStringLiteral("赛车版")},
        {QStringLiteral("Wide"), QStringLiteral("宽体")},
        {QStringLiteral("Option B"), QStringLiteral("选项 B")},
        {QStringLiteral("Option %1"), QStringLiteral("选项 %1")},
    };

    static const QHash<QString, QString> zhTw{
        {QStringLiteral("Livery texture scale"), QStringLiteral("塗裝紋理精度")},
        {QStringLiteral("Cycle model level of detail"), QStringLiteral("切換模型細節層級")},
        {QStringLiteral("Parts"), QStringLiteral("套件")},
        {QStringLiteral("Show body kit and exterior part selection"),
         QStringLiteral("顯示車身套件與外觀零件選擇")},
        {QStringLiteral("Save a snapshot image"), QStringLiteral("儲存快照圖片")},
        {QStringLiteral("Save snapshot"), QStringLiteral("儲存快照")},
        {QStringLiteral("Loading car model…"), QStringLiteral("正在載入車模…")},
        {QStringLiteral("Ready"), QStringLiteral("就緒")},
        {QStringLiteral("Car model failed: %1"), QStringLiteral("車模載入失敗: %1")},
        {QStringLiteral("Livery loaded (no car id — model not loaded)"),
         QStringLiteral("塗裝已載入（無車型 ID，未載入車模）")},
        {QStringLiteral("Unknown car id %1 — model not loaded"),
         QStringLiteral("未知車型 ID %1（未載入車模）")},
        {QStringLiteral("No game folder — livery shown without car model"),
         QStringLiteral("無遊戲目錄 — 僅顯示塗裝，不載入車模")},
        {QStringLiteral("Car archive %1 not found under %2"),
         QStringLiteral("在 %2 下找不到車模包 %1")},
        {QStringLiteral("raster decals: %1"), QStringLiteral("貼花圖集: %1")},
        {QStringLiteral("raster decals: ready"), QStringLiteral("貼花圖集: 就緒")},
        {QStringLiteral("raster decals: no game folder"), QStringLiteral("貼花圖集: 無遊戲目錄")},
        {QStringLiteral("Forza Horizon 6 game folder"),
         QStringLiteral("選擇《極限競速：地平線 6》安裝目錄")},
        {QStringLiteral("Open C_livery"), QStringLiteral("開啟 C_livery")},
        {QStringLiteral("C_livery (C_livery Livery_*.C_livery);;All files (*)"),
         QStringLiteral("C_livery (C_livery Livery_*.C_livery);;所有檔案 (*)")},
        {QStringLiteral("Could not read the livery:\n%1"),
         QStringLiteral("無法讀取塗裝:\n%1")},
        {QStringLiteral("This is not a Forza Horizon 6 install folder (it must contain "
                        "media/Cars and must not be another Forza generation).\n\n"
                        "Please choose again, or cancel."),
         QStringLiteral("這不是《極限競速：地平線 6》的安裝目錄（需要包含 media/Cars，"
                        "且不能是 Forza Horizon 5 等其它世代）。\n\n請重新選擇，或取消。")},
        {QStringLiteral("Decals.zip not found under the game folder"),
         QStringLiteral("遊戲目錄下找不到 Decals.zip")},
        {QStringLiteral("Decals.zip has no decal*.swatchbin entries"),
         QStringLiteral("Decals.zip 中沒有 decal*.swatchbin 項目")},
        {QStringLiteral("invalid swatchbin"), QStringLiteral("無效的 swatchbin")},
        {QStringLiteral("cannot write %1"), QStringLiteral("無法寫入 %1")},
        {QStringLiteral("short write to %1"), QStringLiteral("寫入 %1 不完整")},
        {QStringLiteral("cannot replace %1"), QStringLiteral("無法取代 %1")},
        {QStringLiteral("cannot create temporary directory for %1"),
         QStringLiteral("無法建立暫存目錄: %1")},
        {QStringLiteral("zip: no .carbin found in %1"),
         QStringLiteral("壓縮檔 %1 中沒有 .carbin")},
        {QStringLiteral("PARTS"), QStringLiteral("套件")},
        {QStringLiteral("Only for reference, ingame render may differ"),
         QStringLiteral("僅供參考，遊戲內實際渲染可能不同")},
        {QStringLiteral("No suitable graphics API"), QStringLiteral("無可用的圖形 API")},
        {QStringLiteral("Loading model"), QStringLiteral("正在載入車模")},
        {QStringLiteral("No model found"), QStringLiteral("找不到車模")},
        {QStringLiteral("Hide preview controls"), QStringLiteral("隱藏預覽控制")},
        {QStringLiteral("Show preview controls"), QStringLiteral("顯示預覽控制")},
        {QStringLiteral("Level of detail"), QStringLiteral("細節層級")},
        {QStringLiteral("Select which level of detail is rendered in the preview."),
         QStringLiteral("選擇預覽中要渲染的模型細節層級。")},
        {QStringLiteral("Load a car to choose its model detail."),
         QStringLiteral("載入車模後可選擇其細節層級。")},
        {QStringLiteral("This car only provides one detail level."),
         QStringLiteral("此車型只有一個細節層級。")},
        {QStringLiteral("Body parts"), QStringLiteral("車身套件")},
        {QStringLiteral("Choose the body kit and installed exterior parts."),
         QStringLiteral("選擇車身套件與已安裝的外觀零件。")},
        {QStringLiteral("This car has no selectable exterior part variants."),
         QStringLiteral("此車型沒有可選的外觀零件。")},
        {QStringLiteral("Load a car to see its available parts."),
         QStringLiteral("載入車模後可查看可用零件。")},
        {QStringLiteral("Part"), QStringLiteral("零件")},
        {QStringLiteral("None (Stock)"), QStringLiteral("無（原廠）")},
        {QStringLiteral("Stock"), QStringLiteral("原廠")},
        {QStringLiteral("Body Kit"), QStringLiteral("車身套件")},
        {QStringLiteral("Body Kit %1"), QStringLiteral("車身套件 %1")},
        {QStringLiteral("Race"), QStringLiteral("賽車版")},
        {QStringLiteral("Wide"), QStringLiteral("寬體")},
        {QStringLiteral("Option B"), QStringLiteral("選項 B")},
        {QStringLiteral("Option %1"), QStringLiteral("選項 %1")},
    };

    static const QHash<QString, QString> ja{
        {QStringLiteral("Livery texture scale"), QStringLiteral("デザインのテクスチャ解像度")},
        {QStringLiteral("Cycle model level of detail"),
         QStringLiteral("モデルの詳細レベルを切り替え")},
        {QStringLiteral("Parts"), QStringLiteral("パーツ")},
        {QStringLiteral("Show body kit and exterior part selection"),
         QStringLiteral("ボディパーツと外装パーツの選択を表示")},
        {QStringLiteral("Save a snapshot image"), QStringLiteral("スナップショット画像を保存")},
        {QStringLiteral("Save snapshot"), QStringLiteral("スナップショットを保存")},
        {QStringLiteral("Loading car model…"), QStringLiteral("カーモデルを読み込み中…")},
        {QStringLiteral("Ready"), QStringLiteral("準備完了")},
        {QStringLiteral("Car model failed: %1"),
         QStringLiteral("カーモデルの読み込みに失敗しました: %1")},
        {QStringLiteral("Livery loaded (no car id — model not loaded)"),
         QStringLiteral("デザインを読み込みました（車両 ID なし — モデル未読み込み）")},
        {QStringLiteral("Unknown car id %1 — model not loaded"),
         QStringLiteral("不明な車両 ID %1 — モデル未読み込み")},
        {QStringLiteral("No game folder — livery shown without car model"),
         QStringLiteral("ゲームフォルダーがありません — デザインのみ表示（カーモデルなし）")},
        {QStringLiteral("Car archive %1 not found under %2"),
         QStringLiteral("カーモデル %1 が %2 に見つかりません")},
        {QStringLiteral("raster decals: %1"), QStringLiteral("デカールアトラス: %1")},
        {QStringLiteral("raster decals: ready"), QStringLiteral("デカールアトラス: 準備完了")},
        {QStringLiteral("raster decals: no game folder"),
         QStringLiteral("デカールアトラス: ゲームフォルダーなし")},
        {QStringLiteral("Forza Horizon 6 game folder"),
         QStringLiteral("Forza Horizon 6 のインストールフォルダーを選択")},
        {QStringLiteral("Open C_livery"), QStringLiteral("C_livery を開く")},
        {QStringLiteral("C_livery (C_livery Livery_*.C_livery);;All files (*)"),
         QStringLiteral("C_livery (C_livery Livery_*.C_livery);;すべてのファイル (*)")},
        {QStringLiteral("Could not read the livery:\n%1"),
         QStringLiteral("デザインを読み込めませんでした:\n%1")},
        {QStringLiteral("This is not a Forza Horizon 6 install folder (it must contain "
                        "media/Cars and must not be another Forza generation).\n\n"
                        "Please choose again, or cancel."),
         QStringLiteral("これは Forza Horizon 6 のインストールフォルダーではありません"
                        "（media/Cars を含み、Forza Horizon 5 など別世代でない必要があります）。"
                        "\n\nもう一度選択するか、キャンセルしてください。")},
        {QStringLiteral("Decals.zip not found under the game folder"),
         QStringLiteral("ゲームフォルダーに Decals.zip が見つかりません")},
        {QStringLiteral("Decals.zip has no decal*.swatchbin entries"),
         QStringLiteral("Decals.zip に decal*.swatchbin エントリがありません")},
        {QStringLiteral("invalid swatchbin"), QStringLiteral("無効な swatchbin")},
        {QStringLiteral("cannot write %1"), QStringLiteral("%1 に書き込めません")},
        {QStringLiteral("short write to %1"), QStringLiteral("%1 への書き込みが不完全です")},
        {QStringLiteral("cannot replace %1"), QStringLiteral("%1 を置き換えられません")},
        {QStringLiteral("cannot create temporary directory for %1"),
         QStringLiteral("一時ディレクトリを作成できません: %1")},
        {QStringLiteral("zip: no .carbin found in %1"),
         QStringLiteral("アーカイブ %1 に .carbin がありません")},
        {QStringLiteral("PARTS"), QStringLiteral("パーツ")},
        {QStringLiteral("Only for reference, ingame render may differ"),
         QStringLiteral("参考用です。実際のゲーム内レンダリングとは異なる場合があります")},
        {QStringLiteral("No suitable graphics API"),
         QStringLiteral("使用可能なグラフィックス API がありません")},
        {QStringLiteral("Loading model"), QStringLiteral("カーモデルを読み込み中")},
        {QStringLiteral("No model found"), QStringLiteral("カーモデルが見つかりません")},
        {QStringLiteral("Hide preview controls"),
         QStringLiteral("プレビューコントロールを隠す")},
        {QStringLiteral("Show preview controls"),
         QStringLiteral("プレビューコントロールを表示")},
        {QStringLiteral("Level of detail"), QStringLiteral("詳細レベル")},
        {QStringLiteral("Select which level of detail is rendered in the preview."),
         QStringLiteral("プレビューにレンダリングするモデルの詳細レベルを選択します。")},
        {QStringLiteral("Load a car to choose its model detail."),
         QStringLiteral("カーモデルを読み込むと詳細レベルを選択できます。")},
        {QStringLiteral("This car only provides one detail level."),
         QStringLiteral("この車両には詳細レベルが 1 つだけあります。")},
        {QStringLiteral("Body parts"), QStringLiteral("ボディパーツ")},
        {QStringLiteral("Choose the body kit and installed exterior parts."),
         QStringLiteral("ボディキットと装着済みの外装パーツを選択します。")},
        {QStringLiteral("This car has no selectable exterior part variants."),
         QStringLiteral("この車両には選択可能な外装パーツがありません。")},
        {QStringLiteral("Load a car to see its available parts."),
         QStringLiteral("カーモデルを読み込むと利用可能なパーツを表示できます。")},
        {QStringLiteral("Part"), QStringLiteral("パーツ")},
        {QStringLiteral("None (Stock)"), QStringLiteral("なし（標準）")},
        {QStringLiteral("Stock"), QStringLiteral("標準")},
        {QStringLiteral("Body Kit"), QStringLiteral("ボディキット")},
        {QStringLiteral("Body Kit %1"), QStringLiteral("ボディキット %1")},
        {QStringLiteral("Race"), QStringLiteral("レース仕様")},
        {QStringLiteral("Wide"), QStringLiteral("ワイド")},
        {QStringLiteral("Option B"), QStringLiteral("オプション B")},
        {QStringLiteral("Option %1"), QStringLiteral("オプション %1")},
    };

    static const QHash<QString, QString> ko{
        {QStringLiteral("Livery texture scale"), QStringLiteral("리버리 텍스처 해상도")},
        {QStringLiteral("Cycle model level of detail"),
         QStringLiteral("모델 세부 묘사 수준 전환")},
        {QStringLiteral("Parts"), QStringLiteral("부품")},
        {QStringLiteral("Show body kit and exterior part selection"),
         QStringLiteral("차체 키트 및 외장 부품 선택 표시")},
        {QStringLiteral("Save a snapshot image"), QStringLiteral("스냅샷 이미지 저장")},
        {QStringLiteral("Save snapshot"), QStringLiteral("스냅샷 저장")},
        {QStringLiteral("Loading car model…"), QStringLiteral("차량 모델 불러오는 중…")},
        {QStringLiteral("Ready"), QStringLiteral("준비 완료")},
        {QStringLiteral("Car model failed: %1"),
         QStringLiteral("차량 모델 불러오기 실패: %1")},
        {QStringLiteral("Livery loaded (no car id — model not loaded)"),
         QStringLiteral("리버리를 불러왔습니다(차량 ID 없음 — 모델 미로드)")},
        {QStringLiteral("Unknown car id %1 — model not loaded"),
         QStringLiteral("알 수 없는 차량 ID %1 — 모델 미로드")},
        {QStringLiteral("No game folder — livery shown without car model"),
         QStringLiteral("게임 폴더 없음 — 리버리만 표시(차량 모델 없음)")},
        {QStringLiteral("Car archive %1 not found under %2"),
         QStringLiteral("게임 폴더 %2에서 차량 모델 %1을(를) 찾을 수 없습니다")},
        {QStringLiteral("raster decals: %1"), QStringLiteral("데칼 아틀라스: %1")},
        {QStringLiteral("raster decals: ready"), QStringLiteral("데칼 아틀라스: 준비 완료")},
        {QStringLiteral("raster decals: no game folder"),
         QStringLiteral("데칼 아틀라스: 게임 폴더 없음")},
        {QStringLiteral("Forza Horizon 6 game folder"),
         QStringLiteral("Forza Horizon 6 설치 폴더 선택")},
        {QStringLiteral("Open C_livery"), QStringLiteral("C_livery 열기")},
        {QStringLiteral("C_livery (C_livery Livery_*.C_livery);;All files (*)"),
         QStringLiteral("C_livery (C_livery Livery_*.C_livery);;모든 파일 (*)")},
        {QStringLiteral("Could not read the livery:\n%1"),
         QStringLiteral("리버리를 읽을 수 없습니다:\n%1")},
        {QStringLiteral("This is not a Forza Horizon 6 install folder (it must contain "
                        "media/Cars and must not be another Forza generation).\n\n"
                        "Please choose again, or cancel."),
         QStringLiteral("이 폴더는 Forza Horizon 6 설치 폴더가 아닙니다"
                        "(media/Cars가 있어야 하며 Forza Horizon 5 등 다른 세대여서는 안 됩니다)."
                        "\n\n다시 선택하거나 취소하세요.")},
        {QStringLiteral("Decals.zip not found under the game folder"),
         QStringLiteral("게임 폴더에서 Decals.zip을(를) 찾을 수 없습니다")},
        {QStringLiteral("Decals.zip has no decal*.swatchbin entries"),
         QStringLiteral("Decals.zip에 decal*.swatchbin 항목이 없습니다")},
        {QStringLiteral("invalid swatchbin"), QStringLiteral("잘못된 swatchbin")},
        {QStringLiteral("cannot write %1"), QStringLiteral("%1에 쓸 수 없습니다")},
        {QStringLiteral("short write to %1"), QStringLiteral("%1 쓰기가 불완전합니다")},
        {QStringLiteral("cannot replace %1"), QStringLiteral("%1을(를) 바꿀 수 없습니다")},
        {QStringLiteral("cannot create temporary directory for %1"),
         QStringLiteral("임시 디렉터리를 만들 수 없습니다: %1")},
        {QStringLiteral("zip: no .carbin found in %1"),
         QStringLiteral("압축 파일 %1에 .carbin이 없습니다")},
        {QStringLiteral("PARTS"), QStringLiteral("부품")},
        {QStringLiteral("Only for reference, ingame render may differ"),
         QStringLiteral("참고용이며, 실제 게임 내 렌더링과 다를 수 있습니다")},
        {QStringLiteral("No suitable graphics API"),
         QStringLiteral("사용 가능한 그래픽 API 없음")},
        {QStringLiteral("Loading model"), QStringLiteral("차량 모델 불러오는 중")},
        {QStringLiteral("No model found"), QStringLiteral("차량 모델을 찾을 수 없음")},
        {QStringLiteral("Hide preview controls"),
         QStringLiteral("미리보기 컨트롤 숨기기")},
        {QStringLiteral("Show preview controls"),
         QStringLiteral("미리보기 컨트롤 표시")},
        {QStringLiteral("Level of detail"), QStringLiteral("세부 묘사 수준")},
        {QStringLiteral("Select which level of detail is rendered in the preview."),
         QStringLiteral("미리보기에 렌더링할 모델 세부 묘사 수준을 선택하세요.")},
        {QStringLiteral("Load a car to choose its model detail."),
         QStringLiteral("차량 모델을 불러오면 세부 묘사 수준을 선택할 수 있습니다.")},
        {QStringLiteral("This car only provides one detail level."),
         QStringLiteral("이 차량은 세부 묘사 수준이 하나뿐입니다.")},
        {QStringLiteral("Body parts"), QStringLiteral("차체 부품")},
        {QStringLiteral("Choose the body kit and installed exterior parts."),
         QStringLiteral("차체 키트와 장착된 외장 부품을 선택하세요.")},
        {QStringLiteral("This car has no selectable exterior part variants."),
         QStringLiteral("이 차량에는 선택 가능한 외장 부품이 없습니다.")},
        {QStringLiteral("Load a car to see its available parts."),
         QStringLiteral("차량 모델을 불러오면 사용 가능한 부품을 볼 수 있습니다.")},
        {QStringLiteral("Part"), QStringLiteral("부품")},
        {QStringLiteral("None (Stock)"), QStringLiteral("없음(기본)")},
        {QStringLiteral("Stock"), QStringLiteral("기본")},
        {QStringLiteral("Body Kit"), QStringLiteral("바디 키트")},
        {QStringLiteral("Body Kit %1"), QStringLiteral("바디 키트 %1")},
        {QStringLiteral("Race"), QStringLiteral("레이스용")},
        {QStringLiteral("Wide"), QStringLiteral("와이드")},
        {QStringLiteral("Option B"), QStringLiteral("옵션 B")},
        {QStringLiteral("Option %1"), QStringLiteral("옵션 %1")},
    };

    static const QHash<QString, QString> none;
    if (code == QLatin1String("zh-cn")) {
        return zhCn;
    }
    if (code == QLatin1String("zh-tw")) {
        return zhTw;
    }
    if (code == QLatin1String("ja")) {
        return ja;
    }
    if (code == QLatin1String("ko")) {
        return ko;
    }
    return none;
}

QString tr(const QString &source)
{
    const QHash<QString, QString> &table = chromeTable(languageCode());
    return table.isEmpty() ? source : table.value(source, source);
}

} // namespace viewer_i18n

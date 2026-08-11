# FH6 涂装查看器 (FH6 Livery Viewer)

**[English](README_en.md) | 中文**

在游戏外浏览《极限竞速：地平线 6》本地存档中的涂装：缩略图平铺、显示**具体车型**与**作者**、可缩放的大图预览。解决游戏内涂装管理界面看不到车型、无法预览纹饰的问题。

**只读工具 —— 不会修改任何存档文件。**

## 功能特性

- 🖼️ **缩略图平铺**：直接读取存档自带的 `bigThumb` 预览图，卡片墙展示全部涂装
- 🏎️ **显示具体车型**：内置 651 辆车的 ID 对照表（见致谢），不再对着一串数字猜车
- 🔍 **可缩放预览**：双击打开大图，滚轮缩放（5%–1200%）、拖动平移、原始尺寸/适应窗口
- 📅 **下载日期排序**：按涂装写入存档的时刻排序（新→旧/旧→新），也可按名称/车型/作者排序
- 🔎 **搜索**：按涂装名/作者/车型过滤
- 🏷️ **车型标注**：对照表未收录的新车可手动补名，存入 `cars.json`
- 💾 **一键备份**：把整个存档目录打成 zip（只读操作）
- 🖥️ **双版本存档支持**：Steam 版（userdata）与商店/Xbox 版（`XboxGames\GameSave\pgs`）自动识别

## 环境要求

- Windows 10/11
- Python 3.10+
- [Pillow](https://pypi.org/project/Pillow/)（`pip install pillow`）——用于解码 FH6 存档的 WebP 预览图；不装也能运行，但涂装缩略图无法显示

## 快速开始

```bash
pip install pillow
python app.py
```

启动后自动定位本机 FH6 存档（Steam 读注册表，商店版扫描 `XboxGames\GameSave\pgs`），也可以用「手动选择目录…」指定 `remote` 或 `ContainersRoot` 目录。

## 使用说明

| 操作 | 说明 |
|---|---|
| 单击卡片 | 选中，右侧显示大图与元数据（名称/车型/作者/日期/层数/大小） |
| 双击卡片 | 打开可缩放预览窗口 |
| 「标注车型…」 | 给未收录的车型 ID 补名（写入 `cars.json`，不影响存档） |
| 「备份整个存档」 | 存档目录打包 zip 到 `backups\` |
| 「所在文件夹」 | 在资源管理器中定位该涂装 |

卡片三行信息：涂装名 / 车型名 / 作者。车型未识别时第三行显示 `ID xxxx + 日期`。

## 存档格式说明

工具对存档**只读**。以下格式结论来自本机真实存档验证与社区逆向：

- **Steam 版**：`<Steam>\userdata\<用户ID>\<appid>\remote\`（FH6 appid `2483190`），每个涂装是一组同名分片文件：`Livery_<车型ID>_<- 时间戳>.header / .C_livery / .bigThumb.png …`
- **商店/Xbox 版（pgs）**：`<盘符>:\XboxGames\GameSave\pgs\u_<ID>\<快照号>\ContainersRoot\`，每个涂装是一个同名文件夹（`header` / `C_livery` / `bigThumb.webp`）。`current` junction 指向活跃快照，快照号会滚动（100→101…），工具已自动跟随
- `header` 为二进制元数据（版本/名称/作者/日期/图层数/车型 ID），布局见 `fh6save.py` 注释
- 文件名时间戳为 UTC，界面显示已转为本地时区

## 项目结构

```
├── app.py      # GUI 主程序（只读查看器）
├── fh6save.py  # 存档扫描/header 解析库，可独立运行自检：python fh6save.py
├── cars.json   # 车型 ID → 名称对照表（可手动编辑，用户标注优先）
└── LICENSE     # AGPL-3.0
```

## 上游项目

本项目的存档 header 解析逻辑基于上游项目 **[Arstz/FH6_livery_unlocker](https://github.com/Arstz/FH6_livery_unlocker)**
的逆向成果，并用本机 FH4/FH5/FH6 真实存档做了校正（FH6 header v7 的图层数/车型 ID 偏移为本项目补充）。
遵照上游，本项目同样以 **AGPL-3.0** 开源。

车型 ID 对照表来自 [HDR 的 FH6 Car Ordinals](https://gist.github.com/HDR/0659d1717bc61504bf83750628963f4f)（2026-07-14 版本，651 辆）。

## 免责声明

本工具与 Microsoft、Xbox、Playground Games、Turn 10 无关，Forza 相关商标归其各自所有者。工具仅读取本地存档文件，不提供任何修改、解锁或联机功能。使用本工具产生的任何后果由使用者自行承担。

## License

[GNU Affero General Public License v3.0](LICENSE) —— 与上游项目保持一致。

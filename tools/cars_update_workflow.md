# cars.json 数据维护工作流（2026-11-05 更新版）

> 本文档记录 cars.json 的权威数据源分级、验证工具用法，以及 2026-09-19 / 2026-11-05 两轮修正的证据链。
> 对应代码整改计划见 PLAN.md「改动7」；本工作流已固化为 `tools/update_cars.py`，用法以工具为准（`python tools/update_cars.py [--write]`）。

## 一、数据源角色

| 角色 | 数据源 | 提供什么 | 限制 |
|---|---|---|---|
| 首选命名/年份源 | **游戏内存：GameDB 拍照文案**（`gamemem.read_car_years()`） | （年份, 完整车名）去重表，运行时解密 GameDB | 只认当前界面语言的文案格式（简中为「拍摄 YYYY 年份的 …」，界面语言须简中）；Traffic/NULL 等约 13 条无文案；条数随游戏状态浮动（658~671） |
| 离线兜底（仅新 ID） | **磁盘全名文案**（EN.zip 内 `ChallengeCarCollectionObjective` / `ChallengeTakePhotoObjective` / `ChallengeData` / `ContentOffers`） | `Own the 2025 Porsche Macan GTS` 这类游戏自带完整句子 → （年份, 全名） | 只认完整句子模板；同一全名多个年份时丢弃；只在 ① 未覆盖且 ID **不在 cars.json** 时使用，现存值不覆盖 |
| ID 锚点/新增探测 | **磁盘 `Data_Car.str`**（`<游戏>\media\Stripped\StringTables\EN.zip`） | 车型 ID（GameDB 文案不带 ID，ID 是涂装分组键——结构性必需）；DisplayName 短名仅作 join 键 | 短名非全名（无厂商前缀）；409 辆无年份；同实体可能残留多条（如 4221/4263）；后缀年份偶发与显示年份冲突（2163：后缀 '16，显示 2015）；**品牌展开表与后缀年份推导已废弃（2026-09-19，实证会错）** |
| 人工实证缓存 | cars.json 现存值 | 文案未覆盖条目维持原值（人工实证沉淀） | 无文案覆盖的新 ID 一律报「新增·待人工」，不猜名 |
| 旁证 | 官网 forza.net/fh6cars / 社区表 | 交叉校验 | 官方列表也有年份/命名错误（见 AGENTS.md），不作输入 |

**结论性事实（2026-09-19 实机扫描证实）**：收藏界面显示的年份+品牌全称**不来自** Data_Car.str，
来自运行时解密 GameDB；`.str` 只提供短名表（其内存映射位于 UI 数据附近，内容逐字节一致）。

**2026-09-19 数据源证伪记录**：曾把内存中「' 2015 ' + ' Mercedes-AMG GT S'」判读为收藏 UI「年份串+全名串」
成对结构并列为权威源①——hexdump 实锤其为**拍照文案**「拍摄 2015 年份的 Mercedes-AMG GT S」的 ASCII
可打印碎片："年份的"等非 ASCII 字节在按可打印串提取时被静默丢弃，造成相邻假象；定向 hexprobe 证实
" Toyota GR Yaris" 只存在于中文文案串内部，不存在独立的成对结构。**结论：首选内存数据源 = 拍照
文案**（`gamemem.read_car_years()`）；工具侧 `--ui-pairs`（启发式扫描 0 命中）已与 `ui_pairs()` 一并删除。

**2026-11-05 补充**：游戏自己的挑战/拍照/商店文案是完整句子，不是碎片；用它给**新 ID** 兜底命名
已在本轮 9 辆车上验证。它不替代 GameDB 文案，也不覆盖 cars.json 现存人工实证值。

## 二、验证工具

| 脚本 | 用途 | 前提 |
|---|---|---|
| `tools/update_cars.py` | 磁盘 `.str`（ID 锚点）vs cars.json：ID 集合比对 + 首选内存文案 / 新 ID 磁盘文案 join → diff 报告（默认模式；无文案覆盖的新 ID 报「新增·待人工」） | 只读游戏安装目录；内存数据源需游戏运行（界面语言简中）；离线时自动用磁盘全名文案 |
| `tools/update_car_ids.py` | 扫描 `media/Cars/*.zip` 的 `<CODE>.carbin` 头 `Ordinal`，同步 3D 查看器 `viewer3d/assets/cars/car_ids.json`；`--check` 做覆盖门禁，`--emit-map` 导出纯 ID→模型表，`--allow-short-names` 在名字未定时用 `Data_Car` 短名先落库 | 只读游戏安装目录；不需要游戏运行 |
| `gamemem.py` 直接跑 | `read_car_years()` 拍照文案年份表 | 游戏运行（界面语言简中） |

> 原 `tmp_check_str.py` / `tmp_probe_ui.py` / `tmp_probe_ui2.py` 三个临时脚本已被 `tools/update_cars.py` 取代并删除。

典型节奏：游戏大版本更新 → 优先游戏运行（界面语言简中）跑 `python tools/update_cars.py` 出 diff 报告
（磁盘 .str 解析 + 内存拍照文案年份 join）→ 人工过目 diff 后 `--write` 应用 → `python tools/update_car_ids.py --write`
补齐查看器注册表 → commit → `release_build.py --data-only` 发布（用户端 24h 自动更新，不发版）。
离线时 `update_cars.py` 会改用磁盘全名文案，只给新 ID 补名；`--no-disk-captions` 可强制只信内存文案。

### 纯 ID / 模型路径（不依赖名称，推荐先跑）

`tools/update_car_ids.py` 只用游戏文件里的 `<CODE>.carbin` 头 `Ordinal`，与 `cars.json` 的名称/年份完全解耦：

```bash
python tools/update_car_ids.py --check
# 退出码 0 = 所有游戏 ID 都已在 car_ids.json；1 = 有缺失/待人工/冲突

python tools/update_car_ids.py --allow-short-names --write
# 缺失 ID 一律补入；名字优先 cars.json/磁盘全名文案，仍无则用 Data_Car ModelShort

python tools/update_car_ids.py --emit-map=build/car_id_map.json
# 纯 { "车ID": "模型代码" } 表，便于 diff/审计/给其他工具消费
```

这条路径**只更新 `viewer3d/assets/cars/car_ids.json`**，不写 `cars.json`。`cars.json` 的名称/年份仍走
`tools/update_cars.py`；ID 表可作为交叉校验，二者互不阻塞。`--allow-short-names` 写入的是游戏短名
（如 `GR GT Prototype`），只保证查看器能加载模型，不冒充完整年份全名。



## 三、2026-09-19 修正证据链（对应本次 git diff）

| ID | 改动 | 证据 |
|---|---|---|
| 3624 | `'LuftAuto 002'`→`'Luftauto 002'`（小写） | 内存 UI 全名 `Porsche 911 Carrera Coupe 'Luftauto 002'`；`.str` DisplayName 同为小写 |
| 3088 | 加 `DeBerti` 前缀 | 内存 UI 全名 `2018 DeBerti Chevrolet Silverado 1500 Drift Truck` |
| 3439 | 加 `DeBerti` 前缀 | 内存 UI 全名 `2019 DeBerti Ford Super Duty F-250 Lariat 'Transformer'` |
| 3665 | `SIERRA 700R`→`SIERRA Cars 700R` | 内存 UI 全名 `2021 SIERRA Cars 700R` |
| 3539 / 3540 | 加 `Cars`（同组规律） | 同 make 组 3665 已实证 + 官网同口径；已游戏内目检确认（2026-09-19） |
| 3880 | `GR Corolla`→`Toyota GR Corolla` | 内存 UI 全名 `2023 Toyota GR Corolla` |
| 4221 | 去括号 `GR GT (Prototype)`→`GR GT Prototype` | 内存 UI 富文本 `[BOLD:2025 ]+[BOLD:GR GT Prototype]` + 官网同 |
| 4263 | **删除** `2026 GR GT (Prototype)` | `.str` 中 4221/4263 挂同一对串（ds 均 `GR GT (Prototype)`）；UI 全名表无 2026 版；官网仅 2025；本地 1511+ 涂装无 `Livery_4263_*`（有 1 条 `Livery_4221_*`）→ 判定为游戏改年份后的串表残留 |
| _updated | 打戳 | 须与提交同日（`--data-only` 按日校验） |

**同期反向确认无误的**（此前 `.str` 展开比对曾报"不一致"，实机证实 cars.json 对）：
2242/2471/2654/3064 = `Mercedes-AMG` 口径（`.str` 的 `M-B` 缩写展开成 Mercedes-Benz 是旧规则）；
2163 = 2015（`.str` 后缀 `'16` 与显示年份冲突，按①为准）；278 Vauxhall、3176 Mercedes-Benz AMG Hammer、
3189 Hennessey 前缀、3429 Ginetta、3958 Dodge、3980 Renault、3110 无 DeBerti 前缀（PLAN.md 遗留问题结案）。

## 四、已知残留差异（历史记录，品牌展开表口径已废弃）

> 本节基于已废弃的 BR/FW/MM 品牌展开表口径——品牌展开表已随 2026-09-19 简化移除，本节历史结论已由 GameDB 拍照文案复核取代；重跑工具不再产生此类展开差异。

`.str` 展开规则（apply_all.js 的 BR/FW/MM 品牌表）与 cars.json 的差额，已全部逐条对照内存实证验证为 **cars.json 正确、品牌表口径旧**：

- `M-B`→Mercedes-Benz 映射 ×4（2242/2471/2654/3064）：游戏内为 Mercedes-AMG
- FW 无 `Vaux`→Vauxhall（278）、BR 无 Ginetta（3429）、Dodge 前缀由 MM 表补（3958）、Renault 前缀（3980）
- `AMG`→Mercedes-AMG 映射副作用（3176，游戏内为 Mercedes-Benz AMG Hammer）
- 3189/3441/3539/3540/3665/3880 等的前缀（Hennessey/DeBerti/SIERRA Cars/Toyota）不在 DisplayName 中，依赖 GameDB 全名

## 五、2026-11-05 新增 9 辆（PG 10 月更新）

**ID 锚点（两路互证，691/691 全量实测）**：

- `media/Cars/<CODE>.zip` 内 `<CODE>.carbin` Scene 头的 `Ordinal` 字段 = 存档 `Livery_<ID>_<时间戳>` 的车 ID。
- 同一 zip 的 `Scene/animations/Mojo/clip/carclips_<ID>.clipd` 文件名数字与 `Ordinal` 逐车相同。
- 既有 `viewer3d/assets/cars/car_ids.json` 682 条与游戏模型代码零冲突；新增 9 个 ID 正好是两处都没有的差集。

**命名证据**：以下全名在 `ChallengeCarCollectionObjective.str`、`ChallengeData.str`、`ChallengeTakePhotoObjective.str`（部分含 `ContentOffers.str`）中重复出现，且以 `Data_Car.str` 的 DisplayName 为唯一最长后缀：

| ID | cars.json 新增值 | 模型代码 |
|---|---|---|
| 1325 | 2011 Alfa Romeo Giulietta Quadrifoglio Verde | `ALF_Giulietta_11` |
| 2487 | 2016 Honda S660 Alpha | `HON_S660_16` |
| 3084 | 1966 Hillman Imp | `SUN_Imp_66` |
| 3978 | 1987 FIAT Panda | `FIA_Panda_87` |
| 4086 | 2025 Porsche Macan GTS | `POR_MacanGTS_25` |
| 4181 | 1983 Nissan 240RS | `NIS_00_240RSWRC_83` |
| 4270 | 2025 Ford Mustang GTD | `FOR_MustangGTD_25` |
| 4282 | 2025 Volkswagen Electric Buzz | `VW_IDBuzz_25` |
| 4311 | 2025 Citroën Ami | `CIT_Ami_25` |

**未落库**：`4263 TOY_ScallopID_26` 仍不加入。它与 `4221 TOY_Scallop_26` 的 DisplayName / ModelShort 完全相同（`GR GT (Prototype)`），磁盘文案无法唯一区分；沿用 2026-09-19 的结论（4263 是同实体残留，见第三节）。

**查看器注册表**：`tools/update_car_ids.py --write` 追加上述 9 条到 `viewer3d/assets/cars/car_ids.json`，既有 682 条未改；文件名顺序保持上游“新条目追加末尾”的习惯。

## 六、待办

- （已完成，2026-09-19）固化本工作流为 `tools/update_cars.py`（报告模式默认 + `--write` 应用）；`tmp_*.py` 验证脚本已删除
- （已完成，2026-09-19）数据源简化为 GameDB 系：品牌展开表与后缀年份推导移除（实证会错），无文案覆盖的新 ID 报「新增·待人工」
- （已完成，2026-11-05）磁盘全名文案兜底 + `tools/update_car_ids.py`（carbin `Ordinal` 锚点）
1. 拍照文案与 cars.json 现存歧义（同实体多条、年份冲突）的消重覆盖率

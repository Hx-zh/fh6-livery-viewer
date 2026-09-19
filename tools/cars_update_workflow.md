# cars.json 数据维护工作流（2026-09-19 实机验证版）

> 本文档记录 cars.json 的权威数据源分级、验证工具用法，以及 2026-09-19 一轮修正的完整证据链。
> 对应代码整改计划见 PLAN.md「改动7」；本工作流已固化为 `tools/update_cars.py`，用法以工具为准（`python tools/update_cars.py [--write]`）。

## 一、数据源权威分级（高→低）

| 级别 | 数据源 | 提供什么 | 限制 |
|---|---|---|---|
| ① | **游戏内存：拍照模式文案**（`gamemem.read_car_years()`） | （年份, 完整车名）去重表，运行时解密 GameDB 的唯一内存载体 | 只认当前界面语言的文案格式（简中为「拍摄 YYYY 年份的 …」，界面语言须简中）；Traffic/NULL 等约 13 条无文案；条数随游戏状态浮动（658~671） |
| ② | **磁盘 `Data_Car.str`**（`<游戏>\media\Stripped\StringTables\EN.zip`） | 车型 ID + DisplayName 短名 + ModelShort 缩写（含 `'XX` 年份后缀，671 辆中 262 辆） | 短名非全名（无厂商前缀）；409 辆无年份；同实体可能残留多条（如 4221/4263）；**后缀年份偶发与显示年份冲突**（2163：后缀 '16，显示 2015） |
| ③ | 官网 forza.net/fh6cars / 社区表 | 交叉校验旁证 | 官方列表也有年份/命名错误（见 AGENTS.md），不作输入 |

**结论性事实（2026-09-19 实机扫描证实）**：收藏界面显示的年份+品牌全称**不来自** Data_Car.str，
来自运行时解密 GameDB；`.str` 只提供短名表（其内存映射位于 UI 数据附近，内容逐字节一致）。

**2026-09-19 数据源证伪记录**：曾把内存中「' 2015 ' + ' Mercedes-AMG GT S'」判读为收藏 UI「年份串+全名串」
成对结构并列为权威源①——hexdump 实锤其为**拍照文案**「拍摄 2015 年份的 Mercedes-AMG GT S」的 ASCII
可打印碎片："年份的"等非 ASCII 字节在按可打印串提取时被静默丢弃，造成相邻假象；定向 hexprobe 证实
" Toyota GR Yaris" 只存在于中文文案串内部，不存在独立的成对结构。**结论：权威内存数据源唯一 = 拍照
文案**（`gamemem.read_car_years()`）；工具侧 `--ui-pairs`（启发式扫描 0 命中）已与 `ui_pairs()` 一并删除。

## 二、验证工具

| 脚本 | 用途 | 前提 |
|---|---|---|
| `tools/update_cars.py` | 磁盘 `.str` vs cars.json：ID 集合、名称展开比对、后缀年份冲突 + 内存拍照文案年份 join → diff 报告（默认模式） | 只读游戏安装目录；内存数据源需游戏运行（界面语言简中） |
| `gamemem.py` 直接跑 | `read_car_years()` 拍照文案年份表 | 游戏运行（界面语言简中） |

> 原 `tmp_check_str.py` / `tmp_probe_ui.py` / `tmp_probe_ui2.py` 三个临时脚本已被 `tools/update_cars.py` 取代并删除。

典型节奏：游戏大版本更新 → 游戏运行（界面语言简中）跑 `python tools/update_cars.py` 出 diff 报告
（磁盘 .str 解析 + 内存拍照文案年份 join）→ 人工过目 diff 后 `--write` 应用 → commit →
`release_build.py --data-only` 发布（用户端 24h 自动更新，不发版）。

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

## 四、已知残留差异（重跑 `tmp_check_str.py` 仍会出现，属预期）

`.str` 展开规则（apply_all.js 的 BR/FW/MM 品牌表）与 cars.json 的差额，已全部逐条对照内存 UI 验证为 **cars.json 正确、品牌表口径旧**：

- `M-B`→Mercedes-Benz 映射 ×4（2242/2471/2654/3064）：游戏内为 Mercedes-AMG
- FW 无 `Vaux`→Vauxhall（278）、BR 无 Ginetta（3429）、Dodge 前缀由 MM 表补（3958）、Renault 前缀（3980）
- `AMG`→Mercedes-AMG 映射副作用（3176，游戏内为 Mercedes-Benz AMG Hammer）
- 3189/3441/3539/3540/3665/3880 等的前缀（Hennessey/DeBerti/SIERRA Cars/Toyota）不在 DisplayName 中，依赖 GameDB 全名

## 五、待办

- （已完成，2026-09-19）固化本工作流为 `tools/update_cars.py`（报告模式默认 + `--write` 应用）；`tmp_*.py` 验证脚本已删除
1. 拍照文案与 cars.json 现存歧义（同实体多条、年份冲突）的消重覆盖率

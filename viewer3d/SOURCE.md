# FH6Livery3DViewer — 开源声明 / Open Source Notice

本目录的 3D 查看器（`FH6Livery3DViewer.exe` 及其运行时文件）是自由软件的衍生作品，
随本发布包一同分发。完整对应源码见下方地址，亦可向我们索取。

The 3D viewer in this folder (FH6Livery3DViewer.exe and its runtime files) is a
derivative work of free software. The complete corresponding source is available
at the addresses below.

## 组件与许可 / Components & Licenses

| 组件 | 许可 | 说明 |
|---|---|---|
| FH6LiveryViewer 主程序与查看器壳 | **AGPL-3.0**（见 `AGPL-3.0.txt`） | 本仓库代码 |
| Forza Livery Studio（vendored 快照） | **AGPL-3.0** | 3D 渲染核心，来源 github.com/Arstz/ForzaLiveryStudio（快照提交与本地改动清单见源码仓 `viewer3d/PROVENANCE.md`） |
| Qt 6.8.3（Qt6*.dll，动态链接） | **LGPL-3.0**（见 `LGPL-3.0.txt`） | 源码：download.qt.io / github.com/qt/qt5（tag v6.8.3）。动态链接、未修改，可自行替换这些 DLL |
| zlib（z.dll） | zlib License（见 `zlib.txt`） | github.com/madler/zlib |
| Clipper2（静态链接） | Boost Software License 1.0 | github.com/AngusJohnson/Clipper2 |
| D3Dcompiler_47.dll | Microsoft Windows SDK 可再分发条款 | 随 Windows SDK 分发 |

## 源码 / Source

- GitHub：https://github.com/Hx-zh/fh6-livery-viewer
- Gitee：https://gitee.com/hx_zh/fh6-livery-viewer

`assets/` 内的贴花几何目录与车型 ID 映射表源自对本地游戏文件的解码（随上游 Forza
Livery Studio 的先例以 AGPL 分发）；光栅 logo 纹理缓存不随包分发，由查看器从用户
自己的游戏文件首次运行时构建。

Forza、Horizon 及相关商标归 Microsoft / Playground Games / Turn 10 所有；本工具
与之无关联。

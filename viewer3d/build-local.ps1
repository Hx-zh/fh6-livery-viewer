# viewer3d/build-local.ps1 — 本地一键构建(免开 VS 开发者命令行)。
# 依赖(装一次): VS2022 Build Tools(VCTools) + CMake(Ninja 生成器) +
#   Qt: C:\Qt\6.8.3\msvc2022_64 (aqt install-qt windows desktop 6.8.3 win64_msvc2022_64)
#   zlib: C:\vcpkg (git clone + bootstrap + vcpkg install zlib:x64-windows)
# 用法:  pwsh viewer3d/build-local.ps1 [-Debug] [-Run "<C_livery路径>"]
param(
    [switch]$Debug,
    [string]$Run = ""
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# 定位 VS 的 vcvars64.bat(不依赖 VS Code/开发者命令行)
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) { throw "vswhere 不存在——未安装 VS Build Tools?" }
$vcvars = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
    -find VC\Auxiliary\Build\vcvars64.bat | Select-Object -First 1
if (-not $vcvars) { throw "未找到 vcvars64.bat(VCTools 工作负载未装?)" }

$preset = if ($Debug) { "local-msvc-debug" } else { "local-msvc" }
$buildPreset = if ($Debug) { "local-debug" } else { "local" }

cmd /c "`"$vcvars`" >nul && cmake --preset $preset -S `"$root\viewer3d`" && cmake --build --preset $buildPreset"
if ($LASTEXITCODE -ne 0) { exit 1 }

$exe = Join-Path $root "viewer3d\build\FH6Livery3DViewer.exe"
Write-Host "`n==> $exe"
if ($Run) {
    # 常用: -Run "<存档>\Livery_xxx\C_livery";游戏目录自动探测/记忆
    & $exe $Run
}

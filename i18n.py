# -*- coding: utf-8 -*-
"""i18n.py — 界面多语言(简中默认 + en/ja/ko/繁中)。

语言标签命名遵循 BCP 47(IETF; 语言码 = ISO 639-1 / 国标 GB/T 4880,
地区码 = ISO 3166-1 / GB/T 2659): 简体 = zh-CN, 繁体 = zh-TW,
English = en, 日本語 = ja, 한국어 = ko。

语言判定顺序: 环境变量 FH6_LANG(开发用, 值 zh/en/ja/ko/zhtw 或 BCP 47 形式 zh-CN/zh-TW)
  > exe 文件名后缀(FH6LiveryViewer_zh-CN.exe → 简中; 识别 _zh-CN/_zh-TW/_en/_ja/_ko,
  大小写不敏感) > 默认 zh(简中)。
源码运行时同样可用 FH6_LANG 模拟任意语言。

用法:
    from i18n import tr as _
    _("中文源串")                          # 静态文案
    _("共 {n} 个条目").format(n=n)         # 插值: 源串即 key, 内嵌 {name} 占位符

译文数据在 lang_en.py / lang_ja.py / lang_ko.py / lang_zhtw.py(各含 STRINGS dict,
key = 中文源串)。zh 原样返回; 其他语言缺 key 时回退中文源串(安全兜底)。
"""
import os
import sys

LANGS = ("zh", "en", "ja", "ko", "zhtw")
# exe 文件名后缀(BCP 47, 小写形式) -> 内部语言码
_SUFFIX_LANG = {"_zh-cn": "zh", "_zh-tw": "zhtw",
                "_en": "en", "_ja": "ja", "_ko": "ko"}
# FH6_LANG 环境变量同时接受 BCP 47 形式
_ENV_ALIAS = {"zh-cn": "zh", "zh-tw": "zhtw"}


def _detect_lang() -> str:
    env = os.environ.get("FH6_LANG", "").strip().lower()
    env = _ENV_ALIAS.get(env, env)
    if env in LANGS:
        return env
    exe = getattr(sys, "executable", "") or (sys.argv[0] if sys.argv else "")
    stem = os.path.splitext(os.path.basename(exe))[0].lower()
    for suffix, code in _SUFFIX_LANG.items():
        if stem.endswith(suffix):
            return code
    return "zh"


LANG = _detect_lang()

# 静态导入四个语言文件: PyInstaller 静态分析才能识别并打包它们
# (动态 __import__(f"lang_{LANG}") 分析不到, 打包后会全部回退中文)
import lang_en
import lang_ja
import lang_ko
import lang_zhtw

_TABLES = {"en": lang_en.STRINGS, "ja": lang_ja.STRINGS,
           "ko": lang_ko.STRINGS, "zhtw": lang_zhtw.STRINGS}
_STRINGS: dict = _TABLES.get(LANG, {})


def tr(s: str) -> str:
    """界面文案翻译: 简中原样返回; 其他语言查 STRINGS, 缺 key 回退中文源串。"""
    if LANG == "zh":
        return s
    return _STRINGS.get(s, s)

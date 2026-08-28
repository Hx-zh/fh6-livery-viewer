# -*- coding: utf-8 -*-
"""check_i18n.py — i18n key 提取与语言覆盖校验(仓库工具, 随版本管理)。
改 UI 字符串后必跑: 新增 _() key 必须同步四个 lang 文件, 发布前要求 COVERAGE OK。
用法:
  python check_i18n.py          # 校验四语言文件对 app.py _() key 的覆盖
  python check_i18n.py dump     # 打印全部 key(每行一个, repr)"""
import ast, sys

sys.stdout.reconfigure(encoding="utf-8")

def extract_keys(path="app.py"):
    tree = ast.parse(open(path, encoding="utf-8").read())
    keys, bad = [], []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_" and node.args):
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            keys.append(arg.value)
        else:
            bad.append((node.lineno, ast.dump(arg)[:60]))
    return keys, bad

def main():
    keys, bad = extract_keys()
    uniq = list(dict.fromkeys(keys))
    if bad:
        print(f"!! _() 收到非字面量参数 {len(bad)} 处(不允许):")
        for ln, d in bad:
            print(f"   app.py:{ln}: {d}")
    if len(sys.argv) > 1 and sys.argv[1] == "dump":
        for k in uniq:
            print(repr(k))
        return
    print(f"app.py 共 {len(keys)} 处 _() 调用, 去重 key {len(uniq)} 个\n")
    ok = True
    for lang in ("en", "ja", "ko", "zhtw"):
        try:
            mod = __import__(f"lang_{lang}")
            strings = mod.STRINGS
        except ImportError:
            print(f"lang_{lang}.py: 缺失!")
            ok = False
            continue
        missing = [k for k in uniq if k not in strings]
        extra = [k for k in strings if k not in uniq]
        print(f"lang_{lang}.py: 条目 {len(strings)}, 缺 {len(missing)}, 多余 {len(extra)}")
        for k in missing[:15]:
            print("   缺:", repr(k))
        for k in extra[:10]:
            print("   多:", repr(k))
        if missing or extra:
            ok = False
    # 占位符一致性: 译文的 {name} 集合必须与 key 一致
    import re
    ph = lambda s: sorted(re.findall(r"\{(\w+)\}", s))
    for lang in ("en", "ja", "ko", "zhtw"):
        try:
            strings = __import__(f"lang_{lang}").STRINGS
        except ImportError:
            continue
        for k, v in strings.items():
            if ph(k) != ph(v):
                print(f"!! lang_{lang} 占位符不匹配: {k!r} -> {v!r}")
                ok = False
    print("\n" + ("COVERAGE OK" if ok else "COVERAGE FAILED"))

if __name__ == "__main__":
    main()

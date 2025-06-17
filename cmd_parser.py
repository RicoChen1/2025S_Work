#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
cmd_parser.py  - v0.2
单行命令  →  JSON
依赖: pandas openpyxl
"""

import json
import re
import sys
from pathlib import Path

import pandas as pd


# ─────────────────── ①  读取 & 预处理语法  ──────────────────── #

def load_grammar(xlsx_path: Path):
    """
    把 Excel 中的 [动作] [对象] [属性和参数] 读出来，编译为正则列表
    """
    df = pd.read_excel(xlsx_path, engine="openpyxl")

    # 1) 向下填充，使空白单元格继承上一行数值
    df[["动作", "对象"]] = df[["动作", "对象"]].fillna(method="ffill")

    grammar = []

    for _, row in df.iterrows():
        verb_raw = row.get("动作")
        obj      = row.get("对象")
        template = row.get("属性和参数")

        if pd.isna(template) or pd.isna(verb_raw) or pd.isna(obj):
            continue

        verb_raw = str(verb_raw).strip().lower()
        # 跳过注释行 / 非命令行
        if not re.match(r"^[a-z]", verb_raw):
            continue
        # 如果模板含中文，也直接跳过（说明那行只是说明）
        if re.search(r"[\u4e00-\u9fff]", str(template)):
            continue

        # 2) 把 'add/remove' 之类拆成 ['add', 'remove']
        for verb in re.split(r"[/|,]", verb_raw):
            verb = verb.strip()
            if not verb:
                continue
            try:
                regex = _compile_template(verb, str(obj).strip(), str(template).strip())
            except re.error:
                # 有个别奇葩模板会编不出来，直接忽略
                continue
            grammar.append((verb, obj, regex))

    if not grammar:
        raise RuntimeError("⚠️ 读取 Excel 后没有得到任何有效语法，请检查文件格式")
    return grammar


# ─────────────────── ②  模板 → 正则  ───────────────────── #

_TOKEN  = re.compile(r"<([^<>]+)>")
_ALT    = re.compile(r"\{([^{}]+)\}")
_OPTION = re.compile(r"\[([^\[\]]+)\]")

def _compile_template(verb: str, obj: str, template: str) -> re.Pattern:
    """
    规则转换：
      {a|b}       →  (?:a|b)
      [something] →  (?:something)?
      <name>      →  (?P<name>[^\\s]+)
    并把多空格折叠为 \\s+ ，最后加上 ^…$ 约束整行
    """

    # 1) 处理 {a|b|c}
    template = _ALT.sub(lambda m: "(?:" + "|".join(
        re.escape(x.strip()) for x in m.group(1).split("|") if x.strip()
    ) + ")", template)

    # 2) 暂存 <…> 位置，防止被下一步干扰
    stash = []
    template = _TOKEN.sub(lambda m: f"@@{stash.append(m.group(1)) or len(stash)-1}@@", template)

    # 3) 处理 [optional]
    template = _OPTION.sub(lambda m: f"(?:{m.group(1)})?", template)

    # 4) 把 @@idx@@ 放回，并变成具名捕获
    used = {}
    def restore(m):
        raw = stash[int(m.group(1))]
        key = re.sub(r"[^\w]+", "_", raw.strip())
        if not key or key[0].isdigit():
            key = "arg_" + key              # 组名必须非数字开头
        cnt = used.get(key, 0)
        used[key] = cnt + 1
        if cnt:
            key = f"{key}_{cnt}"
        return fr"(?P<{key}>[^\s]+)"
    template = re.sub(r"@@(\d+)@@", restore, template)

    # 5) 拼最终正则
    pattern = re.sub(r"\s+", r"\\s+", f"{verb} {obj} {template}".strip())
    return re.compile(rf"^{pattern}$", re.IGNORECASE)


# ─────────────────── ③  匹配函数  ─────────────────────── #

def parse_command(cmd: str, grammar):
    cmd = cmd.strip()
    for verb, obj, regex in grammar:
        m = regex.match(cmd)
        if m:
            return {"verb": verb, "object": obj, "args": m.groupdict()}
    return None


# ─────────────────── ④  CLI  ─────────────────────────── #

def main():
    if len(sys.argv) != 2:
        print("Usage: python cmd_parser.py \"<command line>\"")
        sys.exit(1)

    xlsx_path = Path(__file__).with_name("命令树-G.xlsx")
    if not xlsx_path.exists():
        print(f"❌ 找不到语法文件: {xlsx_path}")
        sys.exit(2)

    grammar = load_grammar(xlsx_path)
    result  = parse_command(sys.argv[1], grammar)

    if result is None:
        print("❌ 该命令在语法表里找不到匹配项")
        sys.exit(3)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

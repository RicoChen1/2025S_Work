#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_parser.py  v0.3
  • 解决 pandas FutureWarning
  • 支持把常量关键字一起写进 JSON
    - 默认 nested 结构；改 NESTED=False 可切换为 “占位名\\常量词” 扁平 key
依赖: pandas openpyxl
"""

import json
import re
import sys
from pathlib import Path
from collections import defaultdict

import pandas as pd

# ───────────────── 配置区 ───────────────── #
NESTED = False    # ← 改成 False 即切换成 “占位名\\常量词” 扁平写法. True为分离式写法
# ──────────────────────────────────────── #

# ───── ① Excel → 语法条目  ───── #

def load_grammar(xlsx_path: Path):
    df = pd.read_excel(xlsx_path, engine="openpyxl")

    # 用 .ffill() 消除 FutureWarning
    df[["动作", "对象"]] = df[["动作", "对象"]].ffill()

    grammar = []
    for _, row in df.iterrows():
        verb_field = row.get("动作")
        obj        = row.get("对象")
        template   = row.get("属性和参数")

        if pd.isna(template) or pd.isna(verb_field) or pd.isna(obj):
            continue

        verb_field = str(verb_field).strip().lower()
        if not re.match(r"^[a-z]", verb_field):
            continue
        if re.search(r"[\u4e00-\u9fff]", str(template)):
            continue

        for verb in re.split(r"[/|,]", verb_field):
            verb = verb.strip()
            if not verb:
                continue
            try:
                regex, const_map = _compile_template(
                    verb, str(obj).strip(), str(template).strip()
                )
            except re.error:
                continue
            grammar.append((verb, obj, regex, const_map))

    if not grammar:
        raise RuntimeError("⚠️ 读取 Excel 后没有得到任何有效语法，请检查文件格式")
    return grammar


# ───── ② 把模板编译成正则，并同时记录 “占位符 → 常量词” 对应关系 ───── #

_TOKEN  = re.compile(r"<([^<>]+)>")
_ALT    = re.compile(r"\{([^{}]+)\}")
_OPTION = re.compile(r"\[([^\[\]]+)\]")

def _compile_template(verb: str, obj: str, template: str):
    """
    返回 (regex_obj, mapping_dict)
      mapping_dict: {占位符: 常量词 或 None}
    """

    # ── 先找所有 “word <placeholder>” 形式，记录映射 ── #
    const_map = {}                    # {placeholder: word}
    for m in re.finditer(r"\b(\w+)\s*<([^<> ]+)>", template):
        const_map[m.group(2)] = m.group(1)

    # ── 正则替换规则 ── #
    # 1) {a|b} → (?:a|b)
    template = _ALT.sub(lambda m: "(?:" + "|".join(
        re.escape(x.strip()) for x in m.group(1).split("|") if x.strip()
    ) + ")", template)

    # 2) stash <placeholder>
    stash = []
    template = _TOKEN.sub(lambda m: f"@@{stash.append(m.group(1)) or len(stash)-1}@@", template)

    # 3) [optional] → (?:...)? 
    template = _OPTION.sub(lambda m: f"(?:{m.group(1)})?", template)

    # 4) put placeholders back as named groups
    def restore(m):
        raw = stash[int(m.group(1))]
        key = re.sub(r"[^\w]+", "_", raw.strip())
        if not key or key[0].isdigit():
            key = "arg_" + key
        return fr"(?P<{key}>[^\s]+)"
    template = re.sub(r"@@(\d+)@@", restore, template)

    # 5) final regex
    patt = re.sub(r"\s+", r"\\s+", f"{verb} {obj} {template}".strip())
    regex = re.compile(rf"^{patt}$", re.IGNORECASE)
    return regex, const_map


# ───── ③ 匹配并构造 JSON ───── #

def parse_command(cmd: str, grammar):
    cmd = cmd.strip()
    for verb, obj, regex, const_map in grammar:
        m = regex.match(cmd)
        if not m:
            continue

        raw_args = m.groupdict()

        # 3-A 组装成你想要的 JSON 结构
        if NESTED:
            grouped = defaultdict(dict)
            for k, v in raw_args.items():
                parent = const_map.get(k)
                if parent:
                    grouped[parent][k] = v
                else:                   # 没有常量词的占位符直接放顶层
                    grouped[k] = v
            args_out = dict(grouped)
        else:  # 扁平写法：key = 占位符\常量词 or 占位符
            args_out = {}
            for k, v in raw_args.items():
                prefix = const_map.get(k)
                key = f"{k}\\{prefix}" if prefix else k
                args_out[key] = v

        return {"verb": verb, "object": obj, "args": args_out}

    return None


# ───── ④ CLI ───── #

def main():
    if len(sys.argv) != 2:
        print("Usage:  python cmd_parser.py \"<command line>\"")
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

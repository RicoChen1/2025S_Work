#!/usr/bin/env python
"""
cmd_parser.py
-------------

A **self‑contained** tool that can:
1. Compile your Excel grammar sheet into individual TextFSM template files.
2. Parse a single CLI command using those templates and emit JSON in the
   structure you specified.

Usage
~~~~~
# 1. One‑off compile (only needed when Excel更新)
$ python cmd_parser.py --compile 命令树-G.xlsx templates

# 2. Parse
$ python cmd_parser.py "add syslog host 10.10.11.183 514"

Dependencies (already in your venv)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  pandas openpyxl textfsm tabulate (tabulate is optional, only for pretty prints)

Design notes
~~~~~~~~~~~~
* Skips any row whose **template cell** contains Chinese characters.
* First Excel column (分类) is ignored.
* Fills down verb/object when它们在续行被留空.
* Splits verbs like "bind/unbind" into两行.
* Skips verb=="show".
* Handles尖括号变量 <var> 以及 <A|B|C> 多变量枚举；后者会
  **展开为多份模板**，保证 TextFSM 里变量名固定。
* 固定字面量 OR 枚举 {on|off} 之类 → 捕获到 KEYWORD_1, KEYWORD_2 ...
  以便后续写入 JSON。

Limitations
~~~~~~~~~~~
* 只支持一层可选 [...] 嵌套；已覆盖样例 `lldp [tx {enable|disable}] ...`。
* 如果一行模板内出现逗号/波浪号等符号，它们按字面转义处理。

"""
import argparse
import itertools
import json
import os
import re
import sys
from pathlib import Path

import pandas as pd
import textfsm

TEMPLATE_DIR = Path("templates")  # 默认模板输出目录
SKIP_VERBS = {"show"}

# ---------------------------------------------------------------------------
# Excel → TextFSM  编译部分
# ---------------------------------------------------------------------------

def compile_excel_to_templates(xlsx_path: Path, out_dir: Path = TEMPLATE_DIR):
    """Read Excel grammar and produce *.template files under *out_dir*."""
    out_dir.mkdir(exist_ok=True, parents=True)

    # 读取 & 清洗 -----------------------------------------------------------
    df = pd.read_excel(xlsx_path, header=None, engine="openpyxl").iloc[:, :4]
    df.columns = ["class", "verb", "object", "tmpl"]

    # 续行：verb/object 前向填充
    df["verb"].ffill(inplace=True)
    df["object"].ffill(inplace=True)

    # 丢掉 template 为空 或含中文 的行
    df = df[df["tmpl"].notna()]
    df = df[~df["tmpl"].astype(str).str.contains(r"[\u4e00-\u9fff]")]

    counter = 0
    for _, row in df.iterrows():
        verb = str(row["verb"]).strip()
        if verb.lower() in SKIP_VERBS:
            continue
        obj = str(row["object"]).strip()
        tmpl_raw = str(row["tmpl"]).strip()

        # verb 可能带 / 分隔
        verbs = verb.split("/")
        for vb in verbs:
            vb = vb.strip()
            # 生成模板(含 <A|B|C> 展开)
            for t_content in _expand_template(vb, obj, tmpl_raw):
                filename = f"{counter:04d}_{vb}_{obj}.template"
                (out_dir / filename).write_text(t_content, encoding="utf-8")
                counter += 1

    print(f"✅  Generated {counter} TextFSM templates → {out_dir}\n")

# ---------------------------------------------------------------------------
# 内部：模板字符串 → TextFSM 文件内容
# ---------------------------------------------------------------------------

TOK_SPLIT = re.compile(r"(\s+|[,~])")  # 保留分隔符以便重组

ANG_BR = re.compile(r"^<([^>]+)>$")
CURLY = re.compile(r"^{([^}]+)}$")
SQUARE_START = "["
SQUARE_END = "]"

SPECIAL_CHARS = re.compile(r"([\\^$.+?{}\[\]|()])")

def _escape(text: str) -> str:
    """Escape regex metacharacters in a *literal* token."""
    return SPECIAL_CHARS.sub(r"\\\\\1", text)


def _normalize_var(name: str) -> str:
    return name.strip().replace("-", "_").upper()


def _expand_template(verb: str, obj: str, tmpl: str):
    """Yield one or more full TextFSM template strings for this grammar row."""
    tokens = TOK_SPLIT.split(tmpl)

    # branches用于展开 <A|B|C> 变量名枚举
    branches = [[]]  # 每元素是 list[(kind,data)]

    for tok in tokens:
        if not tok:
            continue
        if tok.isspace() or tok in {",", "~"}:  # 分隔符保留原样
            for b in branches:
                b.append(("LIT", tok))
            continue

        m_ang = ANG_BR.match(tok)
        if m_ang:  # <...>
            inner = m_ang.group(1)
            if "|" in inner:  # 变量名多选
                variants = inner.split("|")
                new_branches = []
                for b in branches:
                    for var in variants:
                        nb = b.copy()
                        nb.append(("VAR", var))
                        new_branches.append(nb)
                branches = new_branches
            else:
                for b in branches:
                    b.append(("VAR", inner))
            continue

        m_curly = CURLY.match(tok)
        if m_curly:  # {on|off}
            choices = m_curly.group(1).split("|")
            for b in branches:
                b.append(("KWCHOICE", choices))
            continue

        # 普通字面量/关键字。直接加入
        for b in branches:
            b.append(("LIT", tok))

    # 生成 TextFSM 文件内容 per branch
    templates = []
    for br in branches:
        values = []          # Value 行顺序
        lines_value = []     # "Value NAME (\S+)" or with choices
        body_tokens = []     # Pattern 里的 token 列表
        kw_counter = 1

        for kind, data in br:
            if kind == "LIT":
                body_tokens.append(_escape(data))
            elif kind == "VAR":
                var = _normalize_var(data)
                if var not in values:
                    lines_value.append(f"Value {var} (\\S+)")
                    values.append(var)
                body_tokens.append(f"${{{var}}}")
            elif kind == "KWCHOICE":
                kw = f"KEYWORD_{kw_counter}"
                kw_counter += 1
                choices_pat = "|".join(map(_escape, data))
                if kw not in values:
                    lines_value.append(f"Value {kw} ({choices_pat})")
                    values.append(kw)
                body_tokens.append(f"${{{kw}}}")
            else:
                raise RuntimeError("Unknown token kind")

        prefix = f"{verb} {obj} "
        pattern_line = f"  ^{_escape(prefix)}{''.join(body_tokens)}$$ -> Record"
        template_txt = "\n".join(lines_value) + "\n\nStart\n" + pattern_line + "\n"
        templates.append(template_txt)

    return templates

# ---------------------------------------------------------------------------
# 运行时解析部分
# ---------------------------------------------------------------------------

def _load_templates(dir_path: Path):
    templates = []  # list[(filename, TextFSM)]
    for file in sorted(dir_path.glob("*.template")):
        with file.open(encoding="utf-8") as fh:
            templates.append((file.name, textfsm.TextFSM(fh)))
    if not templates:
        sys.stderr.write("❌ No templates found. Did you run --compile ?\n")
        sys.exit(1)
    return templates


def parse_command(cmd: str, templates):
    """Try every template until one matches. Return dict or None."""
    for fname, tmpl in templates:
        tmpl.Reset()
        result = tmpl.ParseText(cmd + "\n")  # TextFSM expects a newline at end
        if result:
            headers = [h.lower() for h in tmpl.header]
            record = result[0]
            args = {h: v for h, v in zip(headers, record) if v}
            verb, obj = _extract_verb_obj_from_fname(fname)
            return {
                "verb": verb,
                "object": obj,
                "args": args,
                "template": fname,
            }
    return None


def _extract_verb_obj_from_fname(fname: str):
    # example: "0003_bind_link-group.template" → ("bind", "link-group")
    parts = fname.split("_")
    if len(parts) >= 3:
        return parts[1], parts[2].split(".")[0]
    return "", ""

# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="Compile grammar & parse CLI commands (TextFSM route)")
    p.add_argument("command", nargs="*", help="The command line to parse")
    p.add_argument("--compile", nargs=2, metavar=("EXCEL", "OUTDIR"), help="Compile Excel to templates and exit")
    args = p.parse_args()

    if args.compile:
        excel_path = Path(args.compile[0])
        outdir = Path(args.compile[1])
        compile_excel_to_templates(excel_path, outdir)
        return

    if not args.command:
        p.print_usage(sys.stderr)
        print("\nProvide a command to parse, or use --compile first.")
        sys.exit(1)

    cmd = " ".join(args.command)
    templates = _load_templates(TEMPLATE_DIR)
    parsed = parse_command(cmd, templates)

    if not parsed:
        print("❌  No template matched. Command not recognized.")
        sys.exit(1)

    print(f"# matched template: {parsed['template']}")
    print(json.dumps({
        "verb": parsed["verb"],
        "object": parsed["object"],
        "args": parsed["args"],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

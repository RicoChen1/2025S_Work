#!/usr/bin/env python3
"""
transf.py — Excel → TextFSM template generator

Usage examples
--------------
# 1) 生成 templates 目录下的 .template 文件，并把规范化后的语法写入 syntax.txt
python transf.py 命令树-G.xlsx --syntax-file syntax.txt

# 2) 仅打印到终端，且不生成模板文件
python transf.py 命令树-G.xlsx --no-template

依赖：pandas, openpyxl, textfsm (仅运行时解析时才需 textfsm)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

# ---------------------------------------------------------------------------
# Helpers -------------------------------------------------------------------
# ---------------------------------------------------------------------------

TOKEN_PATTERN = re.compile(r"(<[^>]+>|\{[^}]+\}|\[[^]]+\]|[^\s,]+)")

# Characters that must be escaped in TextFSM fixed tokens
_NEED_ESCAPE = set(r".^$*+?()[{\|")

def _escape(token: str) -> str:
    """Escape regex-special chars inside a fixed token so it stays literal."""
    return "".join(f"\\{c}" if c in _NEED_ESCAPE else c for c in token)


def normalize_row(row: pd.Series, prev_verb: str | None, prev_obj: str | None) -> Tuple[str, str, str] | None:
    """Return (verb, object, template) or None if the row is not a valid template line."""
    # Excel: col0 = category(中文), col1 = verb, col2 = object, col3 = template string
    verb = row.iloc[1] if pd.notna(row.iloc[1]) else prev_verb
    obj = row.iloc[2] if pd.notna(row.iloc[2]) else prev_obj
    template = row.iloc[3]

    if pd.isna(template) or pd.isna(verb) or pd.isna(obj):
        return None
    verb = str(verb).strip()
    obj = str(obj).strip()
    template = str(template).strip()
    if verb.lower() == "show":  # 忽略 show 类命令
        return None
    return verb, obj, template


# ---------------------------------------------------------------------------
# Template generation --------------------------------------------------------
# ---------------------------------------------------------------------------

def slug(name: str) -> str:
    """Make a legal TextFSM variable name: uppercase, non-word -> underscore"""
    return re.sub(r"\W+", "_", name).upper().strip("_") or "VAR"


def convert_tokens(tokens: List[str]) -> Tuple[str, List[str]]:
    """Convert token list into TextFSM regex pattern & collect variable names."""
    var_defs: List[str] = []  # Value lines
    pattern_parts: List[str] = []
    var_count = 1

    def _handle(token: str):
        nonlocal var_count
        # <...> 变量占位符或变量名枚举
        if token.startswith("<") and token.endswith(">"):
            inner = token[1:-1].strip()
            # 如果包含 | 则还是变量；直接给一个占位符名
            vname = slug(inner.split("|")[0])
            if vname not in var_defs:
                var_defs.append(vname)
            pattern_parts.append(f"${{{vname}}}")

        # {a|b|c} 固定关键字枚举
        elif token.startswith("{") and token.endswith("}"):
            opts = token[1:-1].strip()
            pattern_parts.append(f"({opts})")

        # [ ... ] 可选段，递归处理
        elif token.startswith("[") and token.endswith("]"):
            inner = token[1:-1].strip()
            sub_tokens = TOKEN_PATTERN.findall(inner)
            sub_pattern, sub_vars = convert_tokens(sub_tokens)
            # merge new vars (avoid duplicates)
            for v in sub_vars:
                if v not in var_defs:
                    var_defs.append(v)
            pattern_parts.append(f"(?:{sub_pattern})?")

        # 普通固定单词
        else:
            pattern_parts.append(_escape(token))

    for tok in tokens:
        _handle(tok)
    pattern = r"\s+".join(pattern_parts)
    return pattern, var_defs


def build_textfsm_template(cmd_line: str, template_id: str) -> str:
    """Return the full TextFSM template text for a single command line."""
    tokens = TOKEN_PATTERN.findall(cmd_line)
    pattern_body, vars_ = convert_tokens(tokens)

    header_lines = [f"# Auto‑generated template {template_id}"]
    for v in vars_:
        header_lines.append(f"Value {v} (\\S+)")

    header_lines.append("Start")
    header_lines.append(f"  ^{pattern_body}\s*$$ -> Record")

    return "\n".join(header_lines) + "\n"


# ---------------------------------------------------------------------------
# Main driver ----------------------------------------------------------------
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Excel → TextFSM template generator")
    parser.add_argument("excel", help="Path to input .xlsx file")
    parser.add_argument("--templates-dir", default="templates", help="Directory to store .template files")
    parser.add_argument("--syntax-file", help="Write normalized syntax (one per line) to this file")
    parser.add_argument("--no-template", action="store_true", help="Do not write .template files, only list syntax")
    args = parser.parse_args()

    xl_path = Path(args.excel)
    if not xl_path.is_file():
        print(f"[ERROR] Excel file '{xl_path}' not found", file=sys.stderr)
        sys.exit(1)

    df = pd.read_excel(xl_path, header=None, engine="openpyxl")

    records: List[Tuple[str, str, str]] = []
    prev_verb = prev_obj = None
    for _, row in df.iterrows():
        result = normalize_row(row, prev_verb, prev_obj)
        if result is None:
            # keep previous verb/obj if row was blank or skipped
            if pd.notna(row.iloc[1]):
                prev_verb = str(row.iloc[1]).strip()
            if pd.notna(row.iloc[2]):
                prev_obj = str(row.iloc[2]).strip()
            continue
        verb, obj, template = result
        prev_verb, prev_obj = verb, obj
        records.append((verb, obj, template))

    if not records:
        print("[WARN] No valid templates found in Excel", file=sys.stderr)
        sys.exit(0)

    syntax_lines: List[str] = []

    if not args.no_template:
        out_dir = Path(args.templates_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

    for idx, (verb, obj, tpl) in enumerate(records, 1):
        cmd = f"{verb} {obj} {tpl}"
        syntax_lines.append(cmd)

        if not args.no_template:
            template_text = build_textfsm_template(cmd, f"{verb}_{obj}_{idx}")
            file_name = f"{verb}_{obj}_{idx}.template".replace("-", "_")
            with open((Path(args.templates_dir) / file_name), "w", encoding="utf-8") as f:
                f.write(template_text)

    # Write syntax file or print
    if args.syntax_file:
        with open(args.syntax_file, "w", encoding="utf-8") as f:
            for line in syntax_lines:
                f.write(line + "\n")
        print(f"[OK] Wrote {len(syntax_lines)} syntax lines to {args.syntax_file}")
    else:
        print(";\n".join(syntax_lines))

    if not args.no_template:
        print(f"[OK] Generated {len(records)} .template files under '{args.templates_dir}'")


if __name__ == "__main__":
    main()

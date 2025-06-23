#!/usr/bin/env python3
r"""
transf.py — Excel → TextFSM template generator (v0.5)
====================================================
Changelog v0.5 (2025‑06‑22)
---------------------------
* **Slash‑verbs拆分**  `bind/unbind` 这类动词现被自动 **拆成多行**：
  - `bind`、`unbind` 各生成一条模板、一行语法、一个文件。
  - 文件名示例：`bind_link_group_32.template`、`unbind_link_group_33.template`。
* 文件名净化 (v0.4) 与中文/省略号/CRLF 处理逻辑保持不变。

CLI 仍保持一致：
```bash
python transf.py 命令树-G.xlsx              # 生成 templates/*.template
python transf.py 命令树-G.xlsx --no-template  # 仅预览
```
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Tuple

import pandas as pd

# ---------------------------------------------------------------------------
# Helpers & regex -----------------------------------------------------------
# ---------------------------------------------------------------------------

TOKEN_PATTERN = re.compile(r"(<[^>]+>|\{[^}]+\}|\[[^]]+\]|[.,]|[^\s,;]+)")
NEED_ESCAPE   = set(r".^$*+?()[{\\|")
CHINESE_RE    = re.compile(r"[\u4e00-\u9fff]")  # CJK block
FNAME_SAFE    = re.compile(r"[^0-9A-Za-z_]+")     # strip for filenames


def escape_lit(text: str) -> str:
    """Escape regex meta‑chars inside literal CLI tokens."""
    return "".join(f"\\{c}" if c in NEED_ESCAPE else c for c in text)


def has_chinese(s: str) -> bool:
    return bool(CHINESE_RE.search(s))

# ---------------------------------------------------------------------------
# Row → (verb, object, template) list ---------------------------------------
# ---------------------------------------------------------------------------

def parse_row(row: pd.Series, prev_v: str | None, prev_o: str | None) -> List[Tuple[str,str,str]]:
    """Return *zero or more* (verb, obj, tpl) tuples extracted from one sheet row.

    * Handles verb/object carry‑over (续行)。
    * Filters Chinese / `show` rows.
    * **NEW**: splits verbs containing `/` into multiple records.
    """
    verb = row.iloc[1] if pd.notna(row.iloc[1]) else prev_v
    obj  = row.iloc[2] if pd.notna(row.iloc[2]) else prev_o
    tpl  = row.iloc[3]
    if any(pd.isna(x) for x in (verb, obj, tpl)):
        return []

    verb, obj = str(verb).strip(), str(obj).strip()
    tpl = re.sub(r"[\r\n]+", " ", str(tpl)).strip()  # remove internal newlines

    if verb.lower() == "show" or has_chinese(verb + obj + tpl):
        return []

    verbs = [v.strip() for v in verb.split('/') if v.strip()]  # split by '/'
    return [(v, obj, tpl) for v in verbs]

# ---------------------------------------------------------------------------
# Token list → TextFSM regex pattern ---------------------------------------
# ---------------------------------------------------------------------------

def slug(name: str) -> str:
    return re.sub(r"\W+", "_", name).upper().strip("_") or "VAR"


def conv_tokens(tokens: List[str]):  # → (regex_pattern, var_list)
    parts: List[str] = []
    vars_: List[str] = []

    def add_var(v):
        if v not in vars_:
            vars_.append(v)
        parts.append(f"${{{v}}}")

    for tok in tokens:
        if not tok or tok.isspace():
            continue
        if tok == ',':
            parts.append(',')
            continue
        if tok == '...':
            parts.append(r".*")
            continue

        if tok.startswith('<') and tok.endswith('>'):
            v = slug(tok[1:-1].split('|')[0])
            add_var(v)
        elif tok.startswith('{') and tok.endswith('}'):
            inner = tok[1:-1]
            parts.append(r".*" if '...' in inner else f"({inner})")
        elif tok.startswith('[') and tok.endswith(']'):
            inner = tok[1:-1]
            p, sub_vars = conv_tokens(TOKEN_PATTERN.findall(inner))
            for v in sub_vars:
                if v not in vars_:
                    vars_.append(v)
            parts.append(f"(?:{p})?")
        else:
            parts.append(escape_lit(tok))
    return r"\s+".join(parts), vars_


def build_template(cmd_body: str, tpl_id: str) -> str:
    toks = TOKEN_PATTERN.findall(cmd_body)
    pattern, vlist = conv_tokens(toks)
    lines = [f"# Auto-generated {tpl_id}"]
    lines += [f"Value {v} (\\S+)" for v in vlist]
    lines += ["Start", rf"  ^{pattern}\s*$$ -> Record"]
    return "\n".join(lines) + "\n"

# ---------------------------------------------------------------------------
# Main driver ---------------------------------------------------------------
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Excel → TextFSM template generator")
    ap.add_argument("excel", help="Input .xlsx grammar sheet")
    ap.add_argument("--templates-dir", default="templates")
    ap.add_argument("--syntax-file")
    ap.add_argument("--no-template", action="store_true")
    args = ap.parse_args()

    xl = Path(args.excel)
    if not xl.is_file():
        sys.exit(f"file not found: {xl}")

    df = pd.read_excel(xl, header=None, engine="openpyxl")

    records: List[Tuple[str, str, str]] = []
    prev_v = prev_o = None
    for _, row in df.iterrows():
        recs = parse_row(row, prev_v, prev_o)
        if recs:
            prev_v, prev_o = recs[0][0], recs[0][1]  # update carry‑over using first split verb
            records.extend(recs)
        else:
            if pd.notna(row.iloc[1]):
                prev_v = str(row.iloc[1]).strip()
            if pd.notna(row.iloc[2]):
                prev_o = str(row.iloc[2]).strip()

    if not records:
        sys.exit("No valid templates found.")

    if not args.no_template:
        Path(args.templates_dir).mkdir(parents=True, exist_ok=True)

    syntax_lines: List[str] = []

    for idx, (verb, obj, tpl) in enumerate(records, 1):
        body = f"{verb} {obj} {tpl}"
        syntax_lines.append(body + ';')

        if not args.no_template:
            tmpl_txt = build_template(body, f"{verb}_{obj}_{idx}")
            safe_name = FNAME_SAFE.sub('_', f"{verb}_{obj}_{idx}")
            (Path(args.templates_dir) / f"{safe_name}.template").write_text(tmpl_txt, encoding='utf-8')

    # output syntax preview or write file
    if args.syntax_file:
        Path(args.syntax_file).write_text("\n".join(syntax_lines), encoding='utf-8')
        print(f"wrote {len(syntax_lines)} lines → {args.syntax_file}")
    else:
        print("\n".join(syntax_lines))

    if not args.no_template:
        print(f"generated {len(syntax_lines)} .template files → {args.templates_dir}/")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
r"""
transf.py — Excel → TextFSM template generator (v0.6)
====================================================
Changelog v0.6 (2025‑06‑25)
---------------------------
* **元数据生成** `transf.py` 现在为每个 `.template` 文件生成一个配套的 `.json`
  元数据文件。这个文件包含了 `verb`, `object`, `rule`, 和 `variables` 列表，
  为 `parser.py` 提供了必要的上下文信息，以构建结构化的输出。

Changelog v0.5 (2025‑06‑22)
---------------------------
* **Slash‑verbs拆分**  `bind/unbind` 这类动词现被自动 **拆成多行**：
  - `bind`、`unbind` 各生成一条模板、一行语法、一个文件。
  - 文件名示例：`bind_link_group_32.template`、`unbind_link_group_33.template`。
* 文件名净化 (v0.4) 与中文/省略号/CRLF 处理逻辑保持不变。

CLI 仍保持一致：
```bash
python transf.py 命令树-G.xlsx              # 生成 templates/*.template 和 *.json
python transf.py 命令树-G.xlsx --no-template  # 仅预览
```
"""
from __future__ import annotations

import argparse
import json
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

def slug(name: str) -> str:
    """Converts a string into a valid TextFSM variable name."""
    s = re.sub(r"\W+", "_", name).upper().strip("_") or "VAR"
    if s and s[0].isdigit():
        return f"V_{s}"
    return s


def conv_tokens(tokens: List[str], var_counts: dict) -> Tuple[str, List[str]]:
    """
    Recursively converts a list of command tokens into a regex pattern and a list of unique variable names.

    Args:
        tokens: A list of string tokens from the command definition.
        var_counts: A dictionary to track the count of each variable name to ensure uniqueness.

    Returns:
        A tuple containing the regex pattern string and the list of unique variable names.
    """
    parts: List[str] = []
    vars_list: List[str] = []

    for tok in tokens:
        if not tok or tok.isspace():
            continue

        if tok == ',':
            parts.append(',')
            continue
        if tok == '...':
            parts.append(r".*")
            continue

        # Handle <variable>
        if tok.startswith('<') and tok.endswith('>'):
            var_base_name = slug(tok[1:-1].split('|')[0])
            count = var_counts.get(var_base_name, 0) + 1
            var_counts[var_base_name] = count
            unique_var_name = f"{var_base_name}_{count}" if count > 1 else var_base_name
            vars_list.append(unique_var_name)
            parts.append(f'${{{unique_var_name}}}')

        # Handle {choice|list}
        elif tok.startswith('{') and tok.endswith('}'):
            inner = tok[1:-1]
            parts.append(r".*" if '...' in inner else f"({inner})")

        # Handle [optional block]
        elif tok.startswith('[') and tok.endswith(']'):
            inner_tokens = TOKEN_PATTERN.findall(tok[1:-1])
            pattern_part, sub_vars = conv_tokens(inner_tokens, var_counts)
            parts.append(f"(?:{pattern_part})?")
            vars_list.extend(sub_vars)
        
        # Handle literal keywords
        else:
            parts.append(escape_lit(tok))
            
    return r"\s+".join(parts), vars_list


def build_template(cmd_body: str, tpl_id: str) -> Tuple[str, List[str]]:
    """Generates a TextFSM template and returns it with the list of variables."""
    toks = TOKEN_PATTERN.findall(cmd_body)
    # Start the process with an empty counter dictionary for each new command
    pattern, vlist = conv_tokens(toks, {})
    lines = [f"# Auto-generated {tpl_id}"]
    lines += [f"Value {v} (\\S+)" for v in vlist]
    lines.append("")
    lines += ["Start", rf"  ^{pattern}\s*$$ -> Record"]
    template_content = "\n".join(lines) + "\n"
    return template_content, vlist


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
            prev_v, prev_o = recs[0][0], recs[0][1]
            records.extend(recs)
        else:
            if pd.notna(row.iloc[1]):
                prev_v = str(row.iloc[1]).strip()
            if pd.notna(row.iloc[2]):
                prev_o = str(row.iloc[2]).strip()

    if not records:
        sys.exit("No valid templates found.")

    templates_path = Path(args.templates_dir)
    if not args.no_template:
        templates_path.mkdir(parents=True, exist_ok=True)
        # Clean up old files before generating new ones
        for old_file in templates_path.glob("*.template"):
            old_file.unlink()
        for old_file in templates_path.glob("*.json"):
            old_file.unlink()

    syntax_lines: List[str] = []

    for idx, (verb, obj, tpl) in enumerate(records, 1):
        body = f"{verb} {obj} {tpl}"
        syntax_lines.append(body + ';')

        if not args.no_template:
            safe_name = FNAME_SAFE.sub('_', f"{verb}_{obj}_{idx}")
            
            # Generate template and get variables
            tmpl_txt, vlist = build_template(body, safe_name)
            
            # Write .template file
            template_path = Path(args.templates_dir) / f"{safe_name}.template"
            template_path.write_text(tmpl_txt, encoding='utf-8')

            # Create and write .json metadata file
            metadata = {
                "verb": verb,
                "object": obj,
                "rule": tpl,
                "variables": vlist,
                "raw_command_pattern": body,
            }
            meta_path = Path(args.templates_dir) / f"{safe_name}.json"
            with meta_path.open('w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)

    if not args.no_template:
        print(f"generated {len(syntax_lines)} .template and .json files → {args.templates_dir}/")


if __name__ == "__main__":
    main()

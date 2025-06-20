#!/usr/bin/env python3
r"""
transf.py — Excel → TextFSM template generator (v0.3)
====================================================
* **Fix**   : remove `SyntaxWarning` by making top‑level docstring *raw*.
* **Sanitize**: strip CR/LF inside template cells → single‑line CLI.
* **Chinese** : rows containing CJK still filtered out (unchanged).
* **Ellipsis `...`** : if a token equals `...` *or* a brace‑token contains
  literal `...`, interpret it as **wildcard list** (`.*`) so templates with
  “省略号” keep working without manual cleanup.
* **Comma retention**: simple heuristic — when original token contained a
  comma outside brackets, we emit a literal `,` in pattern so positional
  commas are preserved.

Usage (unchanged)
-----------------
```bash
python transf.py 命令树-G.xlsx --syntax-file syntax.txt
auto‑preview: python transf.py 命令树-G.xlsx --no-template
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
# Regex helpers -------------------------------------------------------------
# ---------------------------------------------------------------------------

TOKEN_PATTERN = re.compile(r"(<[^>]+>|\{[^}]+\}|\[[^]]+\]|[.,]|[^\s,;]+)")
NEED_ESCAPE = set(r".^$*+?()[{\\|")
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")  # basic CJK block


def escape_lit(text: str) -> str:
    return "".join(f"\\{c}" if c in NEED_ESCAPE else c for c in text)


def has_chinese(s: str) -> bool:
    return bool(CHINESE_RE.search(s))


# ---------------------------------------------------------------------------
# Row normalisation ---------------------------------------------------------
# ---------------------------------------------------------------------------

def normalize_row(row: pd.Series, prev_v: str | None, prev_o: str | None):
    verb = row.iloc[1] if pd.notna(row.iloc[1]) else prev_v
    obj = row.iloc[2] if pd.notna(row.iloc[2]) else prev_o
    tpl = row.iloc[3]
    if any(pd.isna(x) for x in (verb, obj, tpl)):
        return None

    verb, obj = str(verb).strip(), str(obj).strip()
    tpl = re.sub(r"[\r\n]+", " ", str(tpl)).strip()  # ← remove internal CR/LF

    if verb.lower() == "show" or has_chinese(verb + obj + tpl):
        return None
    return verb, obj, tpl


# ---------------------------------------------------------------------------
# Token → pattern converter --------------------------------------------------
# ---------------------------------------------------------------------------

def slug(name: str) -> str:
    import re as _re
    return _re.sub(r"\W+", "_", name).upper().strip("_") or "VAR"


def conv_tokens(tokens: List[str]):  # → (pattern, var_list)
    parts: List[str] = []
    vars_: List[str] = []

    def add_var(v):
        if v not in vars_:
            vars_.append(v)
        parts.append(f"${{{v}}}")

    for tok in tokens:
        if tok.isspace() or not tok:
            continue
        # Comma or standalone punctuation
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
            if '...' in inner:
                # wildcard list inside braces
                parts.append(r".*")
            else:
                parts.append(f"({inner})")
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
    lines = [f"# Auto‑generated {tpl_id}"] + [f"Value {v} (\\S+)" for v in vlist]
    lines += ["Start", rf"  ^{pattern}\s*$$ -> Record"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main driver ---------------------------------------------------------------
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Excel → TextFSM template generator")
    ap.add_argument("excel", help="Input .xlsx file")
    ap.add_argument("--templates-dir", default="templates")
    ap.add_argument("--syntax-file")
    ap.add_argument("--no-template", action="store_true")
    args = ap.parse_args()

    xl = Path(args.excel)
    if not xl.is_file():
        sys.exit(f"file not found: {xl}")

    df = pd.read_excel(xl, header=None, engine="openpyxl")

    recs: List[Tuple[str, str, str]] = []
    pv = po = None
    for _, r in df.iterrows():
        nr = normalize_row(r, pv, po)
        if nr:
            pv, po = nr[0], nr[1]
            recs.append(nr)
        else:
            if pd.notna(r.iloc[1]):
                pv = str(r.iloc[1]).strip()
            if pd.notna(r.iloc[2]):
                po = str(r.iloc[2]).strip()

    if not recs:
        sys.exit("No valid rows found.")

    if not args.no_template:
        Path(args.templates_dir).mkdir(parents=True, exist_ok=True)

    syntax_out: List[str] = []
    for idx, (verb, obj, tpl) in enumerate(recs, 1):
        body = f"{verb} {obj} {tpl}"
        syntax_out.append(body + ';')
        if not args.no_template:
            t_text = build_template(body, f"{verb}_{obj}_{idx}")
            fname = f"{verb}_{obj}_{idx}.template".replace('-', '_')
            (Path(args.templates_dir)/fname).write_text(t_text, encoding='utf-8')

    if args.syntax_file:
        Path(args.syntax_file).write_text("\n".join(syntax_out), encoding='utf-8')
        print(f"wrote {len(syntax_out)} lines → {args.syntax_file}")
    else:
        print("\n".join(syntax_out))

    if not args.no_template:
        print(f"generated {len(syntax_out)} .template files → {args.templates_dir}/")


if __name__ == "__main__":
    main()

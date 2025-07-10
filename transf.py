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

import openpyxl

# ---------------------------------------------------------------------------
# Helpers & regex -----------------------------------------------------------
# ---------------------------------------------------------------------------

TOKEN_PATTERN = re.compile(r"(<[^>]+>\.\.\.|\[[^\]]+\]|<[^>]+>|{[^}]+}|[^\s]+)")
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

def parse_row(row: tuple, prev_v: str | None, prev_o: str | None) -> List[Tuple[str,str,str]]:
    """Return *zero or more* (verb, obj, tpl) tuples extracted from one sheet row.

    * Handles verb/object carry‑over (续行).
    * Filters Chinese / `show` rows.
    * Splits verbs containing `/` into multiple records.
    * **NEW**: Skips rows with strikethrough or '##' comments in the args column.
    """
    # row is a tuple of openpyxl cells
    verb_cell, obj_cell, tpl_cell = row[1], row[2], row[3]

    # --- Filter based on content and format ---
    tpl_val = str(tpl_cell.value).strip() if tpl_cell.value else ""
    if not tpl_val or tpl_val.startswith("##"):
        return []
    if tpl_cell.font and tpl_cell.font.strike:
        return []

    # --- Extract and carry over values ---
    verb = str(verb_cell.value).strip() if verb_cell.value else prev_v
    obj = str(obj_cell.value).strip() if obj_cell.value else prev_o
    tpl = re.sub(r"[\r\n]+", " ", tpl_val).strip()

    # Pre-process to strip human-readable descriptions from variables
    # e.g., "<ip:list(,) or range(~)>" becomes "<ip>"
    tpl = re.sub(r"<([^:>]+):[^>]+>", r"<\1>", tpl)

    if not all((verb, obj, tpl)):
        return []

    if verb.lower() == "show" or has_chinese(verb + obj + tpl):
        return []

    verbs = [v.strip() for v in verb.split('/') if v.strip()]  # split by '/'
    return [(v, obj, tpl) for v in verbs]

# ---------------------------------------------------------------------------
# Token list → TextFSM regex pattern ---------------------------------------

def slug(name: str, is_choice: bool = False) -> str:
    """Converts a string into a valid TextFSM variable name."""
    if is_choice:
        return "OPTION"
    # Handle compound variables like "ip_addr,ip_mask" -> "IP_ADDR_IP_MASK"
    s = re.sub(r"[\s,]+", "_", name)
    s = re.sub(r"\W+", "", s).upper().strip("_") or "VAR"
    if s and s[0].isdigit():
        return f"V_{s}"
    return s


def get_unique_var_name(base_name: str, counts: dict) -> str:
    """Generates a unique variable name by appending a count if needed."""
    counts[base_name] = counts.get(base_name, 0) + 1
    count = counts[base_name]
    return f"{base_name}_{count}" if count > 1 else base_name


def conv_tokens(tokens: List[str], var_counts: dict, is_optional: bool = False) -> Tuple[str, List[str], List[dict], dict]:
    """
    Recursively converts command tokens into a regex pattern, a flat list of variable names,
    a structured list of token templates, and a map of Value names to their regex.
    """
    parts: List[str] = []
    vars_list: List[str] = []
    token_templates: List[dict] = []
    value_regex_map: dict = {}
    
    for tok in tokens:
        if not tok or tok.isspace():
            continue

        # Optional block: [ ... ]
        if tok.startswith('[') and tok.endswith(']'):
            inner_tokens = TOKEN_PATTERN.findall(tok[1:-1])
            pattern_part, sub_vars, sub_token_templates, value_regex_map_sub = conv_tokens(inner_tokens, var_counts, is_optional=True)
            value_regex_map.update(value_regex_map_sub)

            if parts:
                parts[-1] = f"{parts[-1]}(?:\\s+{pattern_part})?"
            else:
                parts.append(f"(?:{pattern_part})?")
            
            vars_list.extend(sub_vars)
            token_templates.extend(sub_token_templates)
            continue

        # Variable, Choice, or Enum
        if tok.startswith('<') and tok.endswith('>...'):
            content = tok[1:-4].strip()
            var_name = get_unique_var_name(slug(content), var_counts)
            template = {"type": "variable", "name": var_name, "is_list": True, "is_optional": is_optional}
            vars_list.append(var_name)
            token_templates.append(template)
            parts.append(f"${{{var_name}}}")
            value_regex_map[var_name] = r'\S+' # Default regex for variables
            continue

        elif (tok.startswith('<') and tok.endswith('>')) or (tok.startswith('{') and tok.endswith('}')):
            content = tok[1:-1].strip()
            is_choice = '|' in content
            var_name = get_unique_var_name(slug(content, is_choice=is_choice), var_counts)
            template = {"type": "variable", "name": var_name, "is_optional": is_optional}
            if is_choice:
                template["options"] = [o.strip() for o in content.split('|')]
                value_regex_map[var_name] = r'(' + '|'.join(re.escape(o.strip()) for o in content.split('|')) + r')'
            else:
                value_regex_map[var_name] = r'\S+'

            vars_list.append(var_name)
            token_templates.append(template)
            parts.append(f"${{{var_name}}}")

        # Literal keyword
        else:
            if is_optional:
                kw_name = get_unique_var_name("OPT_KW_" + slug(tok), var_counts)
                vars_list.append(kw_name)
                value_regex_map[kw_name] = escape_lit(tok)
                token_templates.append({
                    "type": "keyword",
                    "value": tok,
                    "name": kw_name,
                    "is_optional": True,
                })
                parts.append(f"${{{kw_name}}}")
            else:
                parts.append(escape_lit(tok))
                token_templates.append({"type": "keyword", "value": tok, "is_optional": False})

    return r"\s+".join(parts), vars_list, token_templates, value_regex_map


def build_template(cmd_body: str, verb: str, obj: str, tpl_id: str) -> Tuple[str, List[str], List[dict]]:
    """Generates a TextFSM template and returns it with variables and token templates for args."""
    toks = TOKEN_PATTERN.findall(cmd_body)
    pattern, vlist, all_token_templates, value_regex_map = conv_tokens(toks, {})

    verb_tok_count = len(TOKEN_PATTERN.findall(verb))
    obj_tok_count = len(TOKEN_PATTERN.findall(obj))
    arg_token_templates = all_token_templates[verb_tok_count + obj_tok_count:]

    # --- Generate Value definitions for all variables ---
    value_lines = []
    vset = set(vlist)
    for var_name in vset:
        regex = value_regex_map.get(var_name, r'\S+') # Fallback for safety
        value_lines.append(f"Value {var_name} ({regex})")

    # --- Identify a non-optional keyword to act as an anchor ---
    anchor_token_index = -1
    anchor_keyword_literal = None
    # Iterate backwards to find the last non-optional keyword
    for i in range(len(arg_token_templates) - 1, -1, -1):
        token = arg_token_templates[i]
        if token.get("type") == "keyword" and not token.get("is_optional"):
            anchor_token_index = i
            anchor_keyword_literal = escape_lit(token["value"])
            break

    final_pattern = pattern
    if anchor_keyword_literal:
        # The anchor must not be an optional keyword that we've turned into a Value
        if not any(f"${{{v}}}" in anchor_keyword_literal for v in vlist):
            anchor_name = "ANCHOR_" + slug(arg_token_templates[anchor_token_index]["value"]).upper()
            # Ensure anchor name is unique
            if anchor_name in vlist:
                anchor_name = f"{anchor_name}_2"

            value_lines.append(f"Value {anchor_name} ({anchor_keyword_literal})")
            # Replace the last occurrence of the anchor keyword literal with the variable
            # This is safer than a blind replace
            parts = final_pattern.rsplit(anchor_keyword_literal, 1)
            if len(parts) == 2:
                final_pattern = f"${{{anchor_name}}}".join(parts)
                vlist.append(anchor_name)

    # --- Assemble the final template ---
    lines = [f"# Auto-generated {tpl_id}"]
    lines.extend(sorted(list(set(value_lines)))) # Use set to remove duplicate Value defs
    lines.append("")
    lines += ["Start", rf"  ^{final_pattern}\s*$$ -> Record"]
    template_content = "\n".join(lines) + "\n"

    return template_content, vlist, arg_token_templates


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

    try:
        workbook = openpyxl.load_workbook(xl, data_only=True)
        sheet = workbook.active
    except Exception as e:
        sys.exit(f"Error opening or reading Excel file: {e}")

    records: List[Tuple[str, str, str]] = []
    prev_v = prev_o = None
    for row in sheet.iter_rows(min_row=1):
        # Pass the tuple of cells to parse_row
        recs = parse_row(row, prev_v, prev_o)
        if recs:
            # If a valid record was parsed, update the carry-over values
            prev_v, prev_o = recs[0][0], recs[0][1]
            records.extend(recs)
        else:
            # Otherwise, still update carry-over if the current row has values
            if row[1].value:
                prev_v = str(row[1].value).strip()
            if row[2].value:
                prev_o = str(row[2].value).strip()

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
            
            # Generate template and get variables and token templates
            tmpl_txt, vlist, arg_token_templates = build_template(body, verb, obj, safe_name)
            
            # Write .template file
            template_path = Path(args.templates_dir) / f"{safe_name}.template"
            template_path.write_text(tmpl_txt, encoding='utf-8')

            # Create and write .json metadata file
            metadata = {
                "verb": verb,
                "object": obj,
                "rule": body,  # Use the full command body as the rule
                "arg_token_templates": arg_token_templates,
                "variables": vlist, # Flat list of var names for TextFSM
            }
            meta_path = Path(args.templates_dir) / f"{safe_name}.json"
            with meta_path.open('w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)

    if not args.no_template:
        print(f"generated {len(syntax_lines)} .template and .json files → {args.templates_dir}/")


if __name__ == "__main__":
    main()

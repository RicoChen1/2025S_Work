#!/usr/bin/env python3
"""
parser.py — CLI log parser (v0.2)
==================================

Changelog v0.2:
* Pre-compiles all TextFSM templates at startup for massive performance gain.
* Simplified main loop and improved error handling.

Parses a given log file against a directory of TextFSM templates and
associated .json metadata files. For each successful match, it combines
the parsed data with its metadata to produce a structured JSON output.

Usage:
    python parser.py <logfile>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import textfsm
except ImportError:
    sys.exit("The 'textfsm' library is required. Please install it using: pip install textfsm")





def load_and_compile_templates(templates_dir: Path) -> list[dict]:
    """Loads all .template files, compiles them, and pairs with .json metadata."""
    compiled_templates = []
    for template_path in templates_dir.glob("*.template"):
        meta_path = template_path.with_suffix(".json")
        if not meta_path.is_file():
            print(f"Warning: Skipping '{template_path.name}' (no .json metadata).", file=sys.stderr)
            continue

        try:
            with template_path.open('r', encoding='utf-8') as f_template:
                fsm = textfsm.TextFSM(f_template)
                with meta_path.open('r', encoding='utf-8') as f_meta:
                    metadata = json.load(f_meta)

                compiled_templates.append({
                    "fsm": fsm,
                    "metadata": metadata,
                    "name": template_path.stem
                })
        except Exception as e:
            print(f"Error loading/compiling template '{template_path.name}': {e}", file=sys.stderr)

    return compiled_templates


def main():
    """Main driver for the parser."""
    ap = argparse.ArgumentParser(description="CLI log parser using TextFSM.")
    ap.add_argument("logfile", help="Path to the log file to parse.")
    ap.add_argument("--templates-dir", default="templates", help="Directory for templates.")
    ap.add_argument("-o", "--output", help="Path to save the output JSON file instead of printing to stdout.")
    args = ap.parse_args()

    log_file = Path(args.logfile)
    templates_dir = Path(args.templates_dir)

    if not log_file.is_file():
        sys.exit(f"Error: Log file not found at '{log_file}'")
    if not templates_dir.is_dir():
        sys.exit(f"Error: Templates directory not found at '{templates_dir}'")

    # --- 1. Load and pre-compile all templates ---
    templates = load_and_compile_templates(templates_dir)
    if not templates:
        sys.exit(f"Error: No valid templates found in '{templates_dir}'")

    # --- 2. Sort templates by specificity (most specific first) ---
    def calculate_specificity(template):
        score = 0
        # Access arg_token_templates from the nested metadata
        for token in template.get("metadata", {}).get("arg_token_templates", []):
            if token.get("type") == "keyword" and not token.get("is_optional"):
                score += 3  # Non-optional keywords are highly specific
            else:
                score += 1  # Variables and optional keywords are less specific
        return score

    templates.sort(key=calculate_specificity, reverse=True)

    all_results = [] # To store all parsed results

    # --- 3. Process log file ---
    try:
        with log_file.open('r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue

                # --- 4. Try each pre-compiled template ---
                for template in templates:
                    try:
                        template["fsm"].Reset()
                        parsed_result = template["fsm"].ParseText(line)

                        if parsed_result:
                            metadata = template["metadata"]
                            record = parsed_result[0]
                            # --- Process the match ---
                            parsed_vars = {k: v for k, v in zip(template["fsm"].header, parsed_result[0]) if v}

                            # Build the final token list. Optional tokens that are not present will have a value of None.
                            arg_tokens = []
                            token_templates = metadata.get("arg_token_templates", [])

                            for tpl in token_templates:
                                final_token = tpl.copy()
                                
                                # A token with a 'name' is a Value in the template (variable or optional keyword)
                                if "name" in tpl:
                                    parsed_value = parsed_vars.get(tpl["name"])
                                    final_token["value"] = parsed_value # This will be None if not found
                                    arg_tokens.append(final_token)
                                
                                # A token without a 'name' is a non-optional keyword
                                else:
                                    arg_tokens.append(final_token)

                            result_json = {
                                "verb": metadata.get("verb"),
                                "object": metadata.get("object"),
                                "arg_tokens": arg_tokens,
                                "RAW": line,
                                "RULE": metadata.get("rule"),
                                "matched_template": template["name"]
                            }
                            all_results.append(result_json)
                            break  # Move to the next line once matched

                    except Exception as e:
                        print(f"Error on line {line_num} with template {template['name']}: {e}", file=sys.stderr)
    except Exception as e:
        sys.exit(f"Error reading log file: {e}")

    # --- 5. Write all results to output ---
    output_target = None
    try:
        if args.output:
            output_target = Path(args.output).open('w', encoding='utf-8')
        else:
            output_target = sys.stdout

        for i, result in enumerate(all_results):
            # JSON Lines format: one JSON object per line, no pretty-printing.
            json.dump(result, output_target, ensure_ascii=False)
            output_target.write('\n')
            # Add a blank line for readability, but only when writing to a file.
            if args.output and i < len(all_results) - 1:
                output_target.write('\n')
        
        if args.output:
            print(f"Success: Output written to {args.output}", file=sys.stderr)

    except Exception as e:
        sys.exit(f"Error writing output: {e}")
    finally:
        if args.output and output_target:
            output_target.close()
        elif not args.output:
            # Warn user about potential stdout issues
            print(f"Warning: Printing to stdout on Windows can sometimes garble output. For guaranteed results, use the -o/--output flag to save to a file.", file=sys.stderr)


if __name__ == "__main__":
    main()

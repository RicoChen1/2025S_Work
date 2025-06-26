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

    # --- 2. Process log file ---
    final_output = []
    with log_file.open('r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue

            # --- 3. Try each pre-compiled template ---
            for template in templates:
                try:
                    # Resetting is crucial for reusable FSM objects
                    template["fsm"].Reset()
                    parsed_result = template["fsm"].ParseText(line)

                    if parsed_result:
                        metadata = template["metadata"]
                        record = parsed_result[0]
                        args_dict = dict(zip(metadata['variables'], record))

                        result_json = {
                            "verb": metadata.get("verb"),
                            "object": metadata.get("object"),
                            "args": args_dict,
                            "RAW": line,
                            "RULE": metadata.get("rule"),
                            "matched_template": template["name"]
                        }
                        final_output.append(result_json)
                        break  # Move to the next line once matched

                except Exception as e:
                    print(f"Error on line {line_num} with template {template['name']}: {e}", file=sys.stderr)

    # --- 4. Write final result to file or stdout ---
    output_json = json.dumps(final_output, indent=2, ensure_ascii=False)

    if args.output:
        try:
            output_path = Path(args.output)
            output_path.write_text(output_json, encoding='utf-8')
            print(f"Success: Output written to {output_path}", file=sys.stderr)
        except Exception as e:
            sys.exit(f"Error: Could not write to output file '{args.output}': {e}")
    else:
        # Warn user about potential stdout issues
        print(f"Warning: Printing to stdout on Windows can sometimes garble output. For guaranteed results, use the -o/--output flag to save to a file.", file=sys.stderr)
        print(output_json)


if __name__ == "__main__":
    main()

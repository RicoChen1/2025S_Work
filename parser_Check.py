import json
import argparse
import sys
import re
from pathlib import Path

def normalize_space(text: str) -> str:
    """Replaces all whitespace sequences with a single space and strips."""
    return re.sub(r'\s+', ' ', text).strip()

def reassemble_command(json_obj: dict) -> str:
    """Reassembles a command string from a parsed JSON object."""
    parts = []
    if 'verb' in json_obj:
        parts.append(json_obj['verb'])
    if 'object' in json_obj:
        parts.append(json_obj['object'])
    
    if 'arg_tokens' in json_obj and isinstance(json_obj['arg_tokens'], list):
        for token in json_obj['arg_tokens']:
            if isinstance(token, dict) and 'value' in token:
                # If the token's value is None (or empty string), it was an omitted optional element, so skip it.
                if token['value'] is None or token['value'] == '':
                    continue
                parts.append(str(token['value']))
    
    return normalize_space(" ".join(parts))

def main():
    parser = argparse.ArgumentParser(
        description="Compare a raw log file with its parsed JSON output."
    )
    parser.add_argument("log_file", type=Path, help="Path to the original log file (e.g., sample.txt)")
    parser.add_argument("json_file", type=Path, help="Path to the parsed JSON output file (e.g., output.json)")
    args = parser.parse_args()

    if not args.log_file.is_file():
        sys.exit(f"Error: Log file not found at '{args.log_file}'")
    if not args.json_file.is_file():
        sys.exit(f"Error: JSON file not found at '{args.json_file}'")

    # --- Count valid lines in log file ---
    try:
        with args.log_file.open('r', encoding='utf-8') as f:
            valid_log_lines = sum(1 for line in f if line.strip() and not line.strip().startswith('##'))
    except Exception as e:
        sys.exit(f"Error reading log file: {e}")

    # --- Process JSON file and compare ---
    json_objects_count = 0
    mismatch_count = 0
    mismatches = []

    try:
        with args.json_file.open('r', encoding='utf-8') as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                
                json_objects_count += 1
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    print(f"Warning: Skipping invalid JSON on line {i} of {args.json_file.name}")
                    continue

                if 'RAW' not in data:
                    print(f"Warning: Skipping JSON object on line {i} due to missing 'RAW' field.")
                    continue

                raw_command = normalize_space(data['RAW'])
                reassembled_command = reassemble_command(data)

                if raw_command != reassembled_command:
                    mismatch_count += 1
                    mismatches.append(
                        f"Mismatch found in object #{json_objects_count}:\n" \
                        f"  - RAW        : {raw_command}\n" \
                        f"  - Reassembled: {reassembled_command}\n"
                    )

    except Exception as e:
        sys.exit(f"Error reading or processing JSON file: {e}")

    # --- Print results ---
    print("--- Verification Report ---")
    print(f"Log file: {args.log_file.name}")
    print(f"JSON file: {args.json_file.name}\n")

    if mismatches:
        print("\n".join(mismatches))

    print("--- Summary ---")
    print(f"Valid command lines in log file : {valid_log_lines}")
    print(f"JSON objects in output file     : {json_objects_count}")
    print(f"Mismatched commands             : {mismatch_count}")
    print("---------------------------")

    if mismatch_count == 0 and valid_log_lines == json_objects_count:
        print("\n✅ Verification successful: Counts and contents match.")
    else:
        print("\n❌ Verification failed: Mismatches detected.")

if __name__ == "__main__":
    main()

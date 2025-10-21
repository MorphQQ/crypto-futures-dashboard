#!/usr/bin/env python3
"""
Full Continuity Export (v0.4)
---------------------------------
Scans project directories and exports all source files (Python, JS, React, CSS, HTML, configs, docs)
into a single JSON file with base64-encoded content.

Output: docs/project_data.json
"""

import json
import base64
import os
from pathlib import Path
from datetime import datetime

# === CONFIG ===
PROJECT_ROOT = Path(os.path.dirname(os.path.dirname(__file__))).resolve()
JSON_PATH = PROJECT_ROOT / 'docs' / 'project_data.json'

# Directories to include
CODE_DIRS = [
    'backend/src/futuresboard',
    'frontend/src',
    'backend/tests',
    'frontend/public',
    'docs'
]

# File types to include
ALLOWED_EXTS = [
    '.py', '.js', '.jsx', '.ts', '.tsx', '.json', '.md',
    '.css', '.scss', '.html', '.yml', '.yaml', '.toml', '.txt', '.ini'
]

# Max file size (in bytes) — increase for full project
MAX_FILE_SIZE = 250_000  # ~250 KB

# === LOGIC ===
def gen_code_section():
    files = []
    for dir_rel in CODE_DIRS:
        dir_path = PROJECT_ROOT / dir_rel
        if not dir_path.exists():
            print(f"⚠️  Missing directory: {dir_path}")
            continue
        for file_path in dir_path.rglob('*'):
            if not file_path.is_file():
                continue
            suffix = file_path.suffix.lower()
            if suffix in ALLOWED_EXTS and file_path.stat().st_size < MAX_FILE_SIZE:
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    b64 = base64.b64encode(content.encode()).decode()
                    summary = f"{len(content.splitlines())}L | {file_path.name} | {suffix} | {round(file_path.stat().st_size / 1024, 1)} KB"
                    files.append({
                        "path": str(file_path.relative_to(PROJECT_ROOT)),
                        "type": suffix,
                        "size_kb": round(file_path.stat().st_size / 1024, 1),
                        "content": b64,
                        "summary": summary
                    })
                    print(f"✅ {file_path.name:40} | {summary}")
                except Exception as e:
                    print(f"❌ Read failed {file_path}: {e}")
    return {"files": files}

if __name__ == "__main__":
    # Load existing data if present
    try:
        with open(JSON_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print("Merging into existing project_data.json …")
    except (FileNotFoundError, json.JSONDecodeError):
        data = {"project": {"name": "Crypto Futures Dashboard", "version": "v0.4"}}
        print("Initializing new JSON …")

    # Generate code section
    code_data = gen_code_section()
    data["code"] = code_data
    data["metadata"] = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_files": len(code_data["files"]),
        "max_file_size_kb": round(MAX_FILE_SIZE / 1024, 1)
    }

    # Write JSON
    with open(JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\n🟢 Export Complete: {len(code_data['files'])} files saved → {JSON_PATH}")

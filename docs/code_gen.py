#!/usr/bin/env python3
# Full Code → JSON: Tree scan + read files (raw/base64). Run: python docs/code_gen.py
import json
import base64
import os
from pathlib import Path

PROJECT_ROOT = Path(os.path.dirname(os.path.dirname(__file__))).resolve()  # Docs → root
JSON_PATH = PROJECT_ROOT / 'docs' / 'project_data.json'
CODE_DIRS = ['backend/src/futuresboard', 'frontend/src', 'docs']  # Tease; adj post-tree

def gen_code_section():
    files = []
    exts = ['.py', '.jsx', '.js', '.md', '.json']  # Fixed: Multi-ext list (P3 scale: + .tsx?)
    for dir_path in CODE_DIRS:
        full_dir = PROJECT_ROOT / dir_path
        if not full_dir.exists():
            print(f"Warning: Dir miss {full_dir} - tree check?")
            continue
        for ext in exts:
            for file_path in full_dir.rglob(f'**{ext}'):  # Fixed: **/*.py etc. recursive
                if file_path.is_file() and file_path.stat().st_size < 50000:  # <50kB guard
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                        b64_content = base64.b64encode(content.encode('utf-8')).decode('utf-8')
                        summary = f"{len(content.splitlines())}L: {content[:100]}..."
                        files.append({
                            "path": str(file_path.relative_to(PROJECT_ROOT)),
                            "type": file_path.suffix,
                            "content": b64_content,
                            "summary": summary
                        })
                        print(f"Grabbed: {file_path.name} ({len(content.splitlines())}L)")  # Tease live
                    except Exception as e:
                        print(f"Read fail {file_path}: {e} - Skip?")
    return {"files": files[:10]}  # Top 10 guard

if __name__ == '__main__':
    code_sec = gen_code_section()
    try:
        with open(JSON_PATH, 'r') as f:
            data = json.load(f)
        print(f"Merge OK: Loaded {len(data)} keys")
    except (FileNotFoundError, json.JSONDecodeError):
        data = {"project": {"name": "Crypto Futures Dashboard", "progress": {"P2": "95%", "P3": "20%"}}}
        print("New JSON init")
    data["code"] = code_sec
    with open(JSON_PATH, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Code JSON OK: {len(code_sec['files'])} files; e.g., {code_sec['files'][0]['path'] if code_sec['files'] else 'None'}")
    print(f"Saved: {JSON_PATH}")
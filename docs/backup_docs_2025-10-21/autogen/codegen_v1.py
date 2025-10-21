#!/usr/bin/env python3
"""
codegen_v1.py — Full Continuity Export (DR-P2 Integrated)
----------------------------------------------------------
Scans project directories and exports source files into a single JSON file
with context metadata (phase, uptime) pulled from continuity exports.

Outputs:
 - docs/project_data.json
 - docs/codegen_index.md

Usage:
  cd <project_root>
  python docs/autogen/codegen_v1.py
"""

import json
import base64
import os
from pathlib import Path
from datetime import datetime

# === CONFIG ===
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = PROJECT_ROOT / "docs"
OUTPUT_JSON = DOCS_DIR / "project_data.json"
OUTPUT_MD = DOCS_DIR / "codegen_index.md"

# Directories to include (relative to PROJECT_ROOT)
CODE_DIRS = [
    "backend/src/futuresboard",
    "frontend/src",
    "backend/tests",
    "frontend/public",
    "docs"
]

# File types to include
ALLOWED_EXTS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".md",
    ".css", ".scss", ".html", ".yml", ".yaml", ".toml", ".txt", ".ini"
}

# Max file size (in bytes)
MAX_FILE_SIZE = 1000_000  # ~1 MB by default; adjust as needed

# Context sources (try project_context_v3.json, then continuity_state.json)
CONTEXT_CANDIDATES = [
    DOCS_DIR / "project_context_v3.json",
    DOCS_DIR / "continuity_state.json",
    DOCS_DIR / "continuity_state.md"
]

def load_context():
    ctx = {}
    for p in CONTEXT_CANDIDATES:
        try:
            if p.exists():
                if p.suffix.lower() == ".json":
                    with open(p, "r", encoding="utf-8") as f:
                        ctx = json.load(f)
                        # normalize keys for top-level project metadata
                        if 'phase' in ctx:
                            return ctx
                else:
                    # simple parse from continuity_state.md for a few fields
                    text = p.read_text(encoding="utf-8")
                    # try to extract Last Sync and Phase lines
                    for line in text.splitlines():
                        if line.startswith("Last Sync:"):
                            ctx.setdefault("timestamp", line.split(":",1)[1].strip())
                        if line.startswith("Phase:"):
                            ctx.setdefault("phase", line.split(":",1)[1].strip())
        except Exception:
            pass  # Fixed: Indent matches try (under for loop)
    return ctx

def safe_b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")

def summarize_file(path: Path):
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    lines = len(text.splitlines())
    size_kb = round(path.stat().st_size / 1024, 1)
    return {
        "path": str(path.relative_to(PROJECT_ROOT)),
        "type": path.suffix.lower(),
        "size_kb": size_kb,
        "lines": lines,
        "content": safe_b64(text),
        "summary": f"{lines}L | {path.name} | {path.suffix.lower()} | {size_kb} KB"
    }

def gen_code_section():
    files = []
    for dir_rel in CODE_DIRS:
        dir_path = PROJECT_ROOT / dir_rel
        if not dir_path.exists():
            # skip missing directories
            continue
        for file_path in dir_path.rglob('*'):
            if not file_path.is_file():
                continue
            suffix = file_path.suffix.lower()
            if suffix in ALLOWED_EXTS and file_path.stat().st_size < MAX_FILE_SIZE:
                entry = summarize_file(file_path)
                if entry:
                    files.append(entry)
    return {"files": files}  # Fixed: Ensure crisp indent at func level

def write_json_safe(path: Path, data):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(path)

def write_index_md(path: Path, project_meta, files):
    lines = []
    ts = project_meta.get("last_sync") or project_meta.get("updated") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    version = project_meta.get("version", "v0.0")
    phase = project_meta.get("phase", "Unknown")
    uptime = project_meta.get("uptime_pct", "N/A")
    lines.append(f"# Code Export Index - Crypto Futures Dashboard ({version})")
    lines.append("")
    lines.append(f"_Last updated: {ts}_")
    lines.append("")
    lines.append(f"| File | Type | Lines | Size (KB) | Path |")
    lines.append(f"|------|------|-------:|---------:|------|")
    for f in sorted(files, key=lambda x: x["path"]):
        lines.append(f"| {f['path'].split('/')[-1]} | {f['type']} | {f['lines']} | {f['size_kb']} | {f['path']} |")
    lines.append("")
    lines.append(f"_Total Files: {len(files)} | Phase: {phase} | Uptime: {uptime}_")
    content = "\n".join(lines)
    tmp = path.with_suffix(".tmp.md")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(content)
    tmp.replace(path)

def main():
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    ctx = load_context()
    project_meta = {
        "name": "Crypto Futures Dashboard",
        "version": "v1.0",
        "phase": ctx.get("phase", "Unknown"),
        "uptime_pct": ctx.get("uptime_pct", ctx.get("uptime", "N/A")),
        "backend_status": ctx.get("backend_health", ctx.get("backend", "unknown")),
        "last_sync": ctx.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    }

    code_section = gen_code_section()
    total_files = len(code_section["files"])

    out = {
        "project": project_meta,
        "code": code_section,
        "metadata": {
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_files": total_files,
            "max_file_size_kb": round(MAX_FILE_SIZE / 1024, 1)
        }
    }

    write_json_safe(OUTPUT_JSON, out)
    write_index_md(OUTPUT_MD, project_meta, code_section["files"])

    print(f"Export Complete: {total_files} files saved -> {OUTPUT_JSON}")

if __name__ == "__main__":
    main()
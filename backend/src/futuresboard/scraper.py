# backend/src/futuresboard/scraper.py
from __future__ import annotations

import os
import time
import asyncio
import pathlib
import json
import threading
from queue import Queue, Empty
from datetime import datetime
from dotenv import load_dotenv
import requests

load_dotenv()

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DB_PATH = os.getenv("DB_PATH", str(REPO_ROOT / "backend" / "src" / "futuresboard" / "futures.db"))
INTERVAL = int(os.getenv("INTERVAL", os.getenv("AUTO_SCRAPE_INTERVAL", "30")))

_emit_queue: Queue = Queue()
_emit_thread = None

# === Logging setup ===
LOGS_DIR = pathlib.Path(REPO_ROOT / "logs")
LOGS_DIR.mkdir(parents=True, exist_ok=True)
QUANT_SUMMARY_LOG = LOGS_DIR / "quant_summary.log"


# ------------------------------
# Helpers
# ------------------------------
def _append_quant_log(line: str):
    try:
        with QUANT_SUMMARY_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _log(app, *args, level="info"):
    """
    Enhanced universal logger for futuresboard.
    - Always UTF-8 safe
    - Flushes immediately (so you see live updates)
    - Color-coded for ΔOI / RSI changes
    - Works in PowerShell, VSCode, CMD, Linux terminals
    """

    import sys
    from colorama import Fore, Style, init as colorama_init
    colorama_init(autoreset=True)

    prefix = f"[Continuity:{os.getenv('PHASE', 'P3')}]"
    msg = " ".join(str(a) for a in args)
    text = f"{prefix} {msg}"

    # --- Safe UTF-8 encoding ---
    safe_text = text.encode("utf-8", errors="ignore").decode("utf-8", errors="ignore")

    # --- Auto color selection ---
    color = Style.RESET_ALL

    # Heuristic: if this is a quant summary line, color intelligently
    if "ΔOI" in safe_text or "ΔOI" in msg or "ΔOI" in text:
        if "+" in safe_text and "ΔOI" in safe_text:
            color = Fore.GREEN
        elif "-" in safe_text and "ΔOI" in safe_text:
            color = Fore.RED
    elif "RSI" in safe_text:
        try:
            import re
            match = re.search(r"RSI\s(\d+\.?\d*)", safe_text)
            if match:
                rsi_val = float(match.group(1))
                if rsi_val > 70:
                    color = Fore.RED
                elif rsi_val < 30:
                    color = Fore.GREEN
                else:
                    color = Fore.YELLOW
        except Exception:
            pass

    # --- Logging to Flask logger if available ---
    try:
        if app and getattr(app, "logger", None):
            if level == "warning":
                app.logger.warning(safe_text)
            elif level == "error":
                app.logger.error(safe_text)
            else:
                app.logger.info(safe_text)
    except Exception:
        pass

    # --- Always print to console, colorized ---
    try:
        print(color + safe_text + Style.RESET_ALL)
        sys.stdout.flush()
    except Exception:
        pass

# ------------------------------
# Quant Summary
# ------------------------------
def generate_quant_summary(app, timeframe: str = "5m", per_symbol: int = 3):
    """
    Generate compact per-symbol summary for the latest metrics,
    writing to logs/quant_summary.log and console.
    """
    try:
        from .db import get_latest_metrics, get_metrics_by_symbol
    except Exception:
        _log(app, "[Quant] DB helpers not available for summary", level="warning")
        return

    try:
        recent_rows = get_latest_metrics(limit=200, tf=timeframe)
        if not recent_rows:
            return

        symbols = []
        for r in recent_rows:
            if hasattr(r, "symbol"):
                s = r.symbol
            elif isinstance(r, dict):
                s = r.get("symbol")
            else:
                s = r["symbol"] if "symbol" in r.keys() else None
            if s and s not in symbols:
                symbols.append(s)

        lines = []
        ts = datetime.utcnow().isoformat(timespec="seconds")

        for sym in symbols:
            rows = get_metrics_by_symbol(sym, limit=per_symbol, tf=timeframe)
            if not rows:
                continue

            norm = []
            for r in rows:
                if hasattr(r, "__dict__"):
                    d = {k: v for k, v in r.__dict__.items() if not k.startswith("_")}
                elif isinstance(r, dict):
                    d = r
                else:
                    d = {k: r[k] for k in r.keys()}
                norm.append(d)

            newest = norm[0]
            prev = norm[1] if len(norm) > 1 else None

            oi = newest.get("oi_abs_usd") or 0.0
            vol = newest.get("vol_usd") or 0.0
            rsi = newest.get("rsi") or 50.0
            ls = newest.get(f"global_ls_{timeframe}") or newest.get("global_ls_5m") or None
            ls_prev = prev.get(f"global_ls_{timeframe}") if prev else None
            top_acc = newest.get("top_ls_accounts") or None
            top_acc_prev = prev.get("top_ls_accounts") if prev else None

            def pct(new, old):
                try:
                    if old and old != 0:
                        return (new - old) / old * 100.0
                except Exception:
                    pass
                return 0.0

            oi_delta = pct(oi, prev.get("oi_abs_usd")) if prev else 0.0
            ls_delta = pct(ls, ls_prev) if (ls is not None and ls_prev is not None) else 0.0
            top_acc_delta = pct(top_acc, top_acc_prev) if (top_acc is not None and top_acc_prev is not None) else 0.0

            oi_s = f"${oi:,.0f}" if oi else "$0"
            vol_s = f"${vol/1e9:.2f}B" if vol and vol > 0 else "$0"
            rsi_s = f"{rsi:.2f}"
            ls_s = f"{ls:.3f}" if ls is not None else "N/A"
            topacc_s = f"{top_acc:.3f}" if top_acc is not None else "N/A"

            sign = lambda x: ("+" if x > 0 else "") + f"{x:.2f}%"
            line = f"[Quant {timeframe}] {sym} ΔOI {sign(oi_delta)} | L/S {ls_s} ({sign(ls_delta)}) | TopAcc {topacc_s} ({sign(top_acc_delta)}) | RSI {rsi_s} | Vol {vol_s}"
            lines.append(line)

        if lines:
            header = f"[QuantSummary {timeframe}] {ts} - symbols={len(lines)}"
            _log(app, header)
            _append_quant_log(header)
            for ln in lines:
                _log(app, ln)
                _append_quant_log(ln)
    except Exception as e:
        _log(app, f"[Quant] generate_quant_summary failed: {e}", level="warning")


# ------------------------------
# Socket Emit Worker
# ------------------------------
def emit_worker(socketio):
    _log(None, "Emit worker started")
    idle = 0
    while True:
        try:
            payload = _emit_queue.get(timeout=15)
            if payload and socketio:
                try:
                    socketio.emit("metrics_update", payload)
                    _log(None, f"Emitted {len(payload.get('data', []))} pairs")
                except Exception as e:
                    _log(None, f"Emit error: {e}", level="error")
            _emit_queue.task_done()
            idle = 0
        except Empty:
            idle += 1
            if idle % 8 == 0:
                _log(None, f"Emit worker heartbeat ({idle * 15}s idle)")
            time.sleep(0.25)
        except Exception as e:
            _log(None, f"Emit worker fatal: {e}", level="error")
            time.sleep(1)


# ------------------------------
# Main Scraper Loop
# ------------------------------
def auto_scrape(app):
    """
    Start scraping loop. Safe to call as background task.
    Uses asyncio get_all_metrics() and save via db.save_metrics_v3.
    """
    if getattr(auto_scrape, "_running", False):
        _log(app, "[Scraper] Already running, skipping reinit.")
        return
    auto_scrape._running = True

    try:
        from .metrics import get_all_metrics
        from .db import save_metrics_v3 as save_metrics
        _log(app, "[Scraper] Quant save_metrics_v3 loaded successfully.")
    except Exception as e:
        _log(app, f"Auto-scrape imports failed: {e}", level="warning")
        return

    # Start socket emit worker
    global _emit_thread
    try:
        from .app import socketio
    except Exception:
        socketio = None

    if _emit_thread is None:
        try:
            if socketio and hasattr(socketio, "start_background_task"):
                socketio.start_background_task(lambda: emit_worker(socketio))
            else:
                _emit_thread = threading.Thread(target=emit_worker, args=(socketio,), daemon=True)
                _emit_thread.start()
        except Exception:
            _emit_thread = threading.Thread(target=emit_worker, args=(socketio,), daemon=True)
            _emit_thread.start()

    if "_shared_loop" not in globals():
        globals()["_shared_loop"] = asyncio.new_event_loop()
    loop = globals()["_shared_loop"]

    base_interval = max(5, INTERVAL)
    tfs = ["5m", "15m", "30m", "1h"]
    tf_idx = 0
    backoff = 0.2

    while True:
        tf = tfs[tf_idx % len(tfs)]
        try:
            _log(app, f"Scrape start tf={tf}")
            metrics = loop.run_until_complete(get_all_metrics(tf=tf))
            if metrics:
                try:
                    saved = save_metrics(metrics, timeframe=tf)
                    if saved > 0:
                        sample = metrics[0]
                        oi = sample.get("oi_abs_usd") or 0.0
                        v = sample.get("vol_usd") or 0.0
                        s = sample.get("symbol")
                        _log(app, f"[Quant] Saved {saved} ({tf}) | {s} oi={oi:.0f} vol={v:.0f}")

                        # --- Quant Summary ---
                        try:
                            generate_quant_summary(app, timeframe=tf)
                        except Exception:
                            _log(app, "[Quant] generate_quant_summary error", level="warning")

                except Exception as e:
                    _log(app, f"Save metrics error: {e}", level="warning")

                # --- Emit and sleep ---
                payload = {"data": metrics, "phase": os.getenv("PHASE", "P3"), "timestamp": datetime.utcnow().isoformat(timespec="seconds")}
                try:
                    _emit_queue.put_nowait(payload)
                except Exception:
                    _log(app, "Emit queue full/failed", level="warning")

                sleep_time = max(5, base_interval * 0.6)
                backoff = 0.2
            else:
                _log(app, f"No metrics returned for tf={tf}", level="warning")
                sleep_time = min(120, base_interval * 1.5)

        except Exception as e:
            _log(app, f"Scrape error tf={tf}: {e}", level="error")
            sleep_time = min(120, base_interval * 2)
            backoff = min(backoff * 2, 10)
            time.sleep(backoff + (base_interval * 0.1))

        finally:
            tf_idx += 1
            time.sleep(sleep_time)

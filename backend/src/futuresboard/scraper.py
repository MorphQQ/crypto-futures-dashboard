from __future__ import annotations

import hashlib
import hmac
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from sqlite3 import Error
from urllib.parse import urlencode
import pathlib
import os
import json
import asyncio
from queue import Queue, Empty
import requests  # type: ignore

from flask import current_app
from flask_socketio import SocketIO

# Local config object loader
from .config import Config

# Load .env early for overrides
from dotenv import load_dotenv
load_dotenv()

# Unified DB path and interval from env with sensible defaults.
REPO_ROOT = pathlib.Path(__file__).parent.parent.parent  # repo/backend/src/..
DB_PATH = os.getenv("DB_PATH", str(REPO_ROOT / "backend" / "src" / "futuresboard" / "futures.db"))
INTERVAL = int(os.getenv("INTERVAL", os.getenv("AUTO_SCRAPE_INTERVAL", "30")))

# Global queue for metrics emit (thread-safe)
q = Queue()
emit_thread = None

def _log(app, *args, level="info"):
    """Helper to log via app.logger if available, else print, with continuity phase context."""
    phase = os.getenv("PHASE", "P3 - Weighted OI + Top L/S + Alerts")
    prefix = f"[Continuity:{phase}]"
    msg = " ".join(str(a) for a in args)
    full_msg = f"{prefix} {msg}"

    try:
        if app and getattr(app, "logger", None):
            if level == "info":
                app.logger.info(full_msg)
            elif level == "warning":
                app.logger.warning(full_msg)
            elif level == "error":
                app.logger.error(full_msg)
            else:
                app.logger.debug(full_msg)
        else:
            print(full_msg)
    except Exception:
        print(full_msg)

def emit_worker():
    """Daemon thread: Poll queue and emit to WS clients (efficient, idle-aware)."""
    from .app import socketio
    try:
        from .db import logger
    except Exception:
        logger = None

    phase = os.getenv("PHASE", "P3 - Weighted OI + Top L/S + Alerts")
    _log(None, f"[EmitWorker] started (phase={phase})")
    idle_counter = 0

    while True:
        try:
            metrics = q.get(timeout=15)
            if metrics:
                payload = {
                    "data": metrics,
                    "phase": phase,
                    "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
                    "status": "healthy"
                }
                socketio.emit("metrics_update", payload)
                if logger:
                    logger.info(f"WS emit {len(metrics)} pairs (phase={phase})")
                idle_counter = 0
            q.task_done()
        except Empty:
            idle_counter += 1
            if idle_counter % 8 == 0:  # every ~2 min
                _log(None, f"[EmitWorker] idle heartbeat ({idle_counter * 15}s idle)")
            time.sleep(0.25)  # low CPU sleep
        except Exception as e:
            _log(None, f"[EmitWorker] error: {e}", level="error")
            time.sleep(1)

class HTTPRequestError(Exception):
    def __init__(self, url, code, msg=None):
        self.url = url
        self.code = code
        self.msg = msg

    def __str__(self) -> str:
        return f"Request to {self.url!r} failed. Code: {self.code}; Message: {self.msg}"

def auto_scrape(app):
    """Start auto scrape in a background thread (daemon)."""
    thread = threading.Thread(target=_auto_scrape, args=(app,), daemon=True)
    thread.start()

def _auto_scrape(app):
    """Auto scrape loop: rotate timeframes, fetch metrics via metrics.get_all_metrics(), save via db.save_metrics() and enqueue for WS."""
    global emit_thread
    if emit_thread is None:
        emit_thread = threading.Thread(target=emit_worker, daemon=True)
        emit_thread.start()
        _log(app, "Emit thread started")

    interval = INTERVAL
    tfs = ['5m', '15m', '30m', '1h']
    tf_idx = 0
    last_telemetry = time.time()
    while True:
        _log(app, "Auto scrape routines starting")
        tf = tfs[tf_idx % len(tfs)]
        _log(app, f"Auto scrape routines starting tf={tf}")
        # Lazy import metrics and db save
        try:
            from .metrics import get_all_metrics  # async function
            from .db import save_metrics
        except Exception as e:
            _log(app, f"Auto scrape import error: {e}", level="error")
            if time.time() - last_telemetry > 1800:  # every 30 min
                _log(app, "Continuity telemetry: scraper alive, DB_PATH ok")
                last_telemetry = time.time()
            time.sleep(interval)
            continue

                # Run the async function in new loop to avoid interfering with other asyncio usage
                # Persistent loop reuse (avoids drift)
        if "_shared_loop" not in globals():
            globals()["_shared_loop"] = asyncio.new_event_loop()
        loop = globals()["_shared_loop"]

        try:
            metrics = loop.run_until_complete(get_all_metrics(tf=tf))
            if metrics:
                saved_count = save_metrics(metrics, tf)
                q.put(metrics)
                _log(app, f"[auto] saved {saved_count} metrics tf={tf}")
            else:
                _log(app, f"[auto] no metrics for tf={tf}", level="warning")
        except Exception as e:
            _log(app, f"[auto] error during get_all_metrics: {e}", level="error")
        finally:
            # adaptive sleep: short sleep if many metrics, longer if idle
            sleep_time = min(max(interval, 10), 120)
            if not metrics:
                sleep_time *= 1.5  # idle mode
            time.sleep(sleep_time)
            tf_idx += 1
        
def hashing(query_string, exchange="binance", timestamp=None):
    cfg_path = pathlib.Path(__file__).parent.parent
    cfg = Config.from_config_dir(cfg_path)
    # ensure cfg.DATABASE follows env override if present
    cfg.DATABASE = DB_PATH
    if exchange == "bybit":
        query_string = f"{timestamp}{cfg.API_KEY}5000" + query_string
        return hmac.new(
            bytes(cfg.API_SECRET.encode("utf-8")),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    return hmac.new(
        bytes(cfg.API_SECRET.encode("utf-8")),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

def get_timestamp():
    return int(time.time() * 1000)

def dispatch_request(http_method, signature=None, timestamp=None, app=None):
    """Return a bound requests function for http_method with headers set from config/API_KEY."""
    session = requests.Session()
    cfg_path = pathlib.Path(__file__).parent.parent
    cfg = Config.from_config_dir(cfg_path)
    cfg.DATABASE = DB_PATH
    api_key = getattr(cfg, "API_KEY", None)

    headers = {
        "Content-Type": "application/json;charset=utf-8",
    }
    if api_key:
        headers.update({
            "X-MBX-APIKEY": api_key,
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-SIGN": f"{signature}",
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": f"{timestamp}",
            "X-BAPI-RECV-WINDOW": "5000",
        })
    session.headers.update(headers)

    return {
        "GET": session.get,
        "DELETE": session.delete,
        "PUT": session.put,
        "POST": session.post,
    }.get(http_method, session.get)

def send_signed_request(http_method, url_path, payload=None, exchange="binance", app=None):
    """Send signed request to exchange (Binance/Bybit)."""
    if payload is None:
        payload = {}
    cfg_path = pathlib.Path(__file__).parent.parent
    cfg = Config.from_config_dir(cfg_path)
    cfg.DATABASE = DB_PATH

    if exchange == "binance":
        payload["timestamp"] = get_timestamp()
    # Build ordered query string
    query_string = urlencode(OrderedDict(sorted(payload.items())))
    query_string = query_string.replace("%27", "%22")

    url = f"{cfg.API_BASE_URL}{url_path}?{query_string}"
    if exchange == "binance":
        url += f"&signature={hashing(query_string, exchange)}"

    params = {"url": url, "params": {}}
    try:
        timestamp = get_timestamp()
        func = dispatch_request(http_method, hashing(query_string=query_string, exchange=exchange, timestamp=timestamp), timestamp, app)
        response = func(**params)
        headers = response.headers
        try:
            json_response = response.json()
        except ValueError as e:
            raise HTTPRequestError(url=url, code=-3, msg=f"{e}")
        if isinstance(json_response, dict) and "code" in json_response:
            raise HTTPRequestError(url=url, code=json_response.get("code"), msg=json_response.get("msg"))
        if isinstance(json_response, dict) and "retCode" in json_response:
            if json_response.get("retCode") != 0:
                raise HTTPRequestError(url=url, code=json_response.get("retCode"), msg=json_response.get("retMsg"))
        return headers, json_response
    except requests.exceptions.ConnectionError as e:
        raise HTTPRequestError(url=url, code=-1, msg=str(e))

def send_public_request(url_path, payload=None, app=None):
    """Send public (unsigned) GET request to API_BASE_URL + url_path."""
    if payload is None:
        payload = {}
    query_string = urlencode(payload, True)
    cfg_path = pathlib.Path(__file__).parent.parent
    cfg = Config.from_config_dir(cfg_path)
    cfg.DATABASE = DB_PATH
    api_base_url = cfg.API_BASE_URL
    url = api_base_url + url_path
    if query_string:
        url = url + "?" + query_string
    try:
        func = dispatch_request("GET", app=app)
        response = func(url=url)
        headers = response.headers
        try:
            json_response = response.json()
        except ValueError as e:
            raise HTTPRequestError(url=url, code=-3, msg=str(e))
        if isinstance(json_response, dict) and "code" in json_response:
            raise HTTPRequestError(url=url, code=json_response.get("code"), msg=json_response.get("msg"))
        return headers, json_response
    except requests.exceptions.ConnectionError as e:
        raise HTTPRequestError(url=url, code=-2, msg=str(e))

def create_connection(db_file):
    conn = None
    try:
        conn = sqlite3.connect(db_file)
    except Error as e:
        print(e)
    return conn

def create_table(conn, create_table_sql):
    try:
        c = conn.cursor()
        c.execute(create_table_sql)
    except Error as e:
        print(e)

def db_setup(database, app=None):
    """Create minimal account/positions/orders/income tables and try to call create_metrics_table from db module (SQLAlchemy)."""
    sql_create_income_table = """ CREATE TABLE IF NOT EXISTS income (
                                        IID integer PRIMARY KEY AUTOINCREMENT,
                                        tranId text,
                                        symbol text,
                                        incomeType text,
                                        income real,
                                        asset text,
                                        info text,
                                        time integer,
                                        tradeId integer,
                                        UNIQUE(tranId, incomeType) ON CONFLICT REPLACE
                                    ); """
    sql_create_position_table = """ CREATE TABLE IF NOT EXISTS positions (
                                        PID integer PRIMARY KEY AUTOINCREMENT,
                                        symbol text,
                                        unrealizedProfit real,
                                        leverage integer,
                                        entryPrice real,
                                        positionSide text,
                                        positionAmt real
                                    ); """
    sql_create_account_table = """ CREATE TABLE IF NOT EXISTS account (
                                        AID integer PRIMARY KEY,
                                        totalWalletBalance real,
                                        totalUnrealizedProfit real,
                                        totalMarginBalance real,
                                        availableBalance real,
                                        maxWithdrawAmount real
                                    ); """
    sql_create_orders_table = """ CREATE TABLE IF NOT EXISTS orders (
                                        OID integer PRIMARY KEY AUTOINCREMENT,
                                        origQty real,
                                        price real,
                                        side text,
                                        positionSide text,
                                        status text,
                                        symbol text,
                                        time integer,
                                        type text
                                    ); """

    conn = create_connection(database)

    if conn is not None:
        create_table(conn, sql_create_income_table)
        create_table(conn, sql_create_position_table)
        create_table(conn, sql_create_account_table)
        create_table(conn, sql_create_orders_table)
        conn.close()
    else:
        _log(None, "Error! cannot create the database connection.")

    # Try to create metrics table via db module (SQLAlchemy preferred)
    try:
        from .db import create_metrics_table  # type: ignore
        if app:
            with app.app_context():
                create_metrics_table()
            _log(app, "Metrics table created/verified OK")
        else:
            # If no app passed, raise to fallback raw SQL
            raise ImportError("No app for context – fallback to raw")
    except ImportError:
        _log(None, "Metrics DB support missing—install/update db.py")
    except Exception as e:
        _log(None, f"Metrics table setup error: {e}", level="warning")
        # Fallback raw SQL
        conn = create_connection(database)
        if conn:
            try:
                c = conn.cursor()
                c.execute("""
                    CREATE TABLE IF NOT EXISTS metrics (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        symbol TEXT NOT NULL,
                        oi_abs_usd FLOAT,
                        global_ls_5m FLOAT,
                        oi_delta_pct FLOAT,
                        UNIQUE(symbol, timestamp) ON CONFLICT REPLACE
                    );
                """)
                conn.commit()
                _log(None, "Metrics table created via raw SQL fallback")
            except sqlite3.Error as se:
                _log(None, f"Raw SQL error: {se}", level="error")
            finally:
                conn.close()

# -- DB helper CRUD for account/orders/income/positions (kept as before) --

def create_income(conn, income):
    sql = """ INSERT INTO income(tranId, symbol, incomeType, income, asset, info, time, tradeId)
              VALUES(?,?,?,?,?,?,?,?) """
    cur = conn.cursor()
    cur.execute(sql, income)

def select_latest_income(conn):
    cur = conn.cursor()
    cur.execute("SELECT time FROM income ORDER BY time DESC LIMIT 0, 1")
    return cur.fetchone()

def select_latest_income_symbol(conn, symbol):
    cur = conn.cursor()
    cur.execute(
        "SELECT time FROM income WHERE symbol = ? ORDER BY time DESC LIMIT 0, 1",
        (symbol,),
    )
    return cur.fetchone()

def create_position(conn, position):
    sql = """ INSERT INTO positions(unrealizedProfit, leverage, entryPrice, positionAmt, symbol, positionSide) VALUES(?,?,?,?,?,?) """
    cur = conn.cursor()
    cur.execute(sql, position)

def update_position(conn, position):
    sql = """ UPDATE positions SET unrealizedProfit = ?, leverage = ?, entryPrice = ?, positionAmt = ? WHERE symbol = ? AND positionSide = ? """
    cur = conn.cursor()
    cur.execute(sql, position)

def delete_all_positions(conn):
    sql = """ DELETE FROM positions """
    cur = conn.cursor()
    cur.execute(sql)
    conn.commit()

def select_position(conn, symbol):
    cur = conn.cursor()
    cur.execute(
        "SELECT unrealizedProfit FROM positions WHERE symbol = ? AND positionSide = ? LIMIT 0, 1",
        (
            symbol[0],
            symbol[1],
        ),
    )
    return cur.fetchone()

def create_account(conn, account):
    sql = """ INSERT INTO account(totalWalletBalance, totalUnrealizedProfit, totalMarginBalance, availableBalance, maxWithdrawAmount, AID) VALUES(?,?,?,?,?,?) """
    cur = conn.cursor()
    cur.execute(sql, account)

def update_account(conn, account):
    sql = """ UPDATE account SET totalWalletBalance = ?, totalUnrealizedProfit = ?, totalMarginBalance = ?, availableBalance = ?, maxWithdrawAmount = ? WHERE AID = ?"""
    cur = conn.cursor()
    cur.execute(sql, account)

def select_account(conn):
    cur = conn.cursor()
    cur.execute("SELECT totalWalletBalance FROM account LIMIT 0, 1")
    return cur.fetchone()

def delete_all_orders(conn):
    sql = """ DELETE FROM orders """
    cur = conn.cursor()
    cur.execute(sql)
    conn.commit()

def create_orders(conn, orders):
    sql = """ INSERT INTO orders(origQty, price, side, positionSide, status, symbol, time, type) VALUES(?,?,?,?,?,?,?,?) """
    cur = conn.cursor()
    cur.execute(sql, orders)

# High level scrape wrapper that handles errors and then calls metrics enrichment + save
def scrape(app=None):
    try:
        _scrape(app=app)
    except HTTPRequestError as exc:
        if app is None:
            _log(None, exc)
        else:
            _log(app, f"{exc}", level="error")
    except Exception as e:
        if app is None:
            _log(None, f"Scrape error: {e}")
        else:
            _log(app, f"Scrape error: {e}", level="error")

    # Lazy metrics hook post-scrape
    try:
        from .metrics import get_all_metrics
        from .db import save_metrics
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        metrics = loop.run_until_complete(get_all_metrics())
        try:
            saved_count = save_metrics(metrics)
        except TypeError:
            saved_count = save_metrics(metrics, None)
        loop.close()
        if app is None:
            _log(None, f"Saved {saved_count} metrics (CLI mode)")
        else:
            _log(app, f"Saved {saved_count} metrics")
        q.put(metrics)
    except ImportError as ie:
        if app is None:
            _log(None, f"Metrics import failed (CLI): {ie}")
        else:
            _log(app, f"Metrics import failed: {ie}", level="warning")
    except Exception as m_e:
        if app is None:
            _log(None, f"Metrics error (CLI): {m_e}")
        else:
            _log(app, f"Metrics error: {m_e}", level="error")

# Main scrape implementation (kept Binance and Bybit flows mostly unchanged, but using unified DB_PATH)
def _scrape(app=None):
    start = time.time()
    cfg_path = pathlib.Path(__file__).parent.parent
    cfg = Config.from_config_dir(cfg_path)
    # Respect environment DB_PATH override
    cfg.DATABASE = DB_PATH

    db_setup(cfg.DATABASE, app)

    up_to_date = False
    weightused = 0
    processed, updated_positions, new_positions, updated_orders, sleeps = 0, 0, 0, 0, 0

    exchange_str = str(cfg.EXCHANGE).lower() if getattr(cfg, "EXCHANGE", None) else ""
    if 'binance' in exchange_str:
        # get open orders
        try:
            if weightused < 800:
                responseHeader, responseJSON = send_signed_request("GET", "/fapi/v1/openOrders", app=app)
                weightused = int(responseHeader.get("X-MBX-USED-WEIGHT-1M", 0))

                with create_connection(cfg.DATABASE) as conn:
                    delete_all_orders(conn)
                    for order in responseJSON:
                        updated_orders += 1
                        row = (
                            float(order.get("origQty", 0)),
                            float(order.get("price", 0)),
                            order.get("side"),
                            order.get("positionSide"),
                            order.get("status"),
                            order.get("symbol"),
                            int(order.get("time", 0)),
                            order.get("type"),
                        )
                        create_orders(conn, row)
                    conn.commit()
        except Exception as e:
            _log(app, f"Binance openOrders error: {e}", level="warning")

        # account and positions
        try:
            responseHeader, responseJSON = send_signed_request("GET", "/fapi/v2/account", app=app)
            weightused = int(responseHeader.get("X-MBX-USED-WEIGHT-1M", weightused or 0))
            overweight = False
            try:
                positions = responseJSON["positions"]
            except Exception:
                overweight = True

            if not overweight:
                with create_connection(cfg.DATABASE) as conn:
                    totals_row = (
                        float(responseJSON.get("totalWalletBalance", 0)),
                        float(responseJSON.get("totalUnrealizedProfit", 0)),
                        float(responseJSON.get("totalMarginBalance", 0)),
                        float(responseJSON.get("availableBalance", 0)),
                        float(responseJSON.get("maxWithdrawAmount", 0)),
                        1,
                    )
                    accountCheck = select_account(conn)
                    if accountCheck is None:
                        create_account(conn, totals_row)
                    elif float(accountCheck[0]) != float(responseJSON.get("totalWalletBalance", 0)):
                        update_account(conn, totals_row)

                    delete_all_positions(conn)
                    for position in positions:
                        position_row = (
                            float(position.get("unrealizedProfit", 0)),
                            int(position.get("leverage", 0)),
                            float(position.get("entryPrice", 0)),
                            float(position.get("positionAmt", 0)),
                            position.get("symbol"),
                            position.get("positionSide"),
                        )
                        create_position(conn, position_row)
                        updated_positions += 1
                    conn.commit()
        except Exception as e:
            _log(app, f"Binance account/positions error: {e}", level="warning")

        # process income/trades until up-to-date
        while not up_to_date:
            if weightused > 800:
                message = f"Weight used: {weightused}/800\nProcessed: {processed}\nSleep: 1 minute"
                _log(app, message)
                sleeps += 1
                time.sleep(60)

            with create_connection(cfg.DATABASE) as conn:
                startTime = select_latest_income(conn)
                if startTime is None:
                    startTime = int(datetime.fromisoformat("2020-01-01 00:00:00+00:00").timestamp() * 1000)
                else:
                    startTime = startTime[0]

                params = {"startTime": startTime + 1, "limit": 1000}
                try:
                    responseHeader, responseJSON = send_signed_request(http_method="GET", url_path="/fapi/v1/income", payload=params, app=app)
                    weightused = int(responseHeader.get("X-MBX-USED-WEIGHT-1M", weightused or 0))

                    if not responseJSON or len(responseJSON) == 0:
                        up_to_date = True
                    else:
                        for income in responseJSON:
                            # guard tradeId missing/empty
                            trade_id = income.get("tradeId", 0) or 0
                            income_row = (
                                int(income.get("tranId", 0)),
                                income.get("symbol"),
                                income.get("incomeType"),
                                income.get("income"),
                                income.get("asset"),
                                income.get("info"),
                                int(income.get("time", 0)),
                                int(trade_id),
                            )
                            create_income(conn, income_row)
                            processed += 1
                        conn.commit()
                except HTTPRequestError as ex:
                    _log(app, f"Income fetch HTTPRequestError: {ex}", level="warning")
                    up_to_date = True
                except Exception as e:
                    _log(app, f"Income fetch error: {e}", level="warning")
                    up_to_date = True

    elif 'bybit' in exchange_str:
        all_symbols = []
        exec_type = {
            "Trade": "REALIZED_PNL",
            "Funding": "FUNDING_FEE",
            "AdlTrade": "ADLTRADE",
            "BustTrade": "BUSTTRADE",
        }

        try:
            params = {"category": "linear", "limit": 200, "settleCoin": "USDT"}
            responseHeader, responseJSON = send_signed_request(http_method="GET", url_path="/v5/position/list", payload=params, exchange="bybit", app=app)
            if isinstance(responseJSON, dict) and "rate_limit_status" in responseJSON:
                weightused = int(responseJSON.get("rate_limit_status", weightused or 0))
        except Exception as e:
            _log(app, f"Bybit position list error: {e}", level="warning")
            responseJSON = {}

        with create_connection(cfg.DATABASE) as conn:
            message = "Deleting orders and positions from db"
            _log(app, message)
            delete_all_orders(conn)
            delete_all_positions(conn)
            message = "Loading orders and positions from exchange"
            _log(app, message)

            try:
                if "result" in responseJSON and "list" in responseJSON["result"]:
                    for position in responseJSON["result"]["list"]:
                        if weightused > 50:
                            message = f"Weight used: {weightused}/{120-weightused}\nProcessed: {updated_positions + new_positions + updated_orders}\nSleep: 1 minute"
                            _log(app, message)
                            sleeps += 1
                            time.sleep(60)
                            weightused = 0

                        if position.get("symbol") not in all_symbols:
                            all_symbols.append(position.get("symbol"))

                        if float(position.get("size", 0)) > 0:
                            position_sides = {"buy": "LONG", "sell": "SHORT"}
                            positionside = position_sides.get(position.get("side", "").lower(), "LONG")

                            position_row = (
                                float(position.get("unrealisedPnl", 0)),
                                int(position.get("leverage", 0)),
                                float(position.get("avgPrice", 0)),
                                float(position.get("size", 0)),
                                position.get("symbol"),
                                positionside,
                            )

                            create_position(conn, position_row)
                            updated_positions += 1

                            params = {"symbol": position.get("symbol"), "category": "linear"}
                            try:
                                responseHeader2, responseJSON2 = send_signed_request(http_method="GET", url_path="/v5/order/realtime", payload=params, exchange="bybit", app=app)
                                if isinstance(responseJSON2, dict) and "rate_limit_status" in responseJSON2:
                                    weightused = int(responseJSON2.get("rate_limit_status", weightused or 0))
                                else:
                                    weightused += 1
                                if "result" in responseJSON2 and "list" in responseJSON2["result"]:
                                    for order in responseJSON2["result"]["list"]:
                                        updated_orders += 1
                                        orderside = order.get("side", "").upper()
                                        row = (
                                            float(order.get("qty", 0)),
                                            float(order.get("price", 0)),
                                            orderside,
                                            positionside,
                                            order.get("orderStatus"),
                                            order.get("symbol"),
                                            int(order.get("createdTime", 0)),
                                            order.get("orderType"),
                                        )
                                        create_orders(conn, row)
                                else:
                                    _log(app, "Orders: 'list' not in responseJSON2['result']", level="warning")
                            except Exception as e:
                                _log(app, f"Bybit realtime orders error: {e}", level="warning")
                else:
                    _log(app, "Positions: 'list' not in responseJSON['result']", level="warning")
            except Exception as e:
                _log(app, f"Bybit parse error: {e}", level="warning")

            # Wallet balance update
            try:
                params = {"coin": "USDT", "accountType": "CONTRACT"}
                responseHeader3, responseJSON3 = send_signed_request(http_method="GET", url_path="/v5/account/wallet-balance", payload=params, exchange="bybit", app=app)
                message = "Updating wallet balance from exchange"
                _log(app, message)
                if "result" in responseJSON3 and "list" in responseJSON3["result"]:
                    maintenance_val = responseJSON3["result"]["list"][0].get("totalMaintenanceMargin", "")
                    maintenance_margin = 0.0 if maintenance_val == "" else float(maintenance_val)
                    coin0 = responseJSON3["result"]["list"][0].get("coin", [{}])[0]
                    totals_row = (
                        float(coin0.get("walletBalance", 0)),
                        float(coin0.get("unrealisedPnl", 0)),
                        maintenance_margin,
                        float(coin0.get("availableToWithdraw", 0)),
                        float(0),
                        1,
                    )
                    accountCheck = select_account(conn)
                    if accountCheck is None:
                        create_account(conn, totals_row)
                    elif float(accountCheck[0]) != float(coin0.get("walletBalance", 0)):
                        update_account(conn, totals_row)
                    conn.commit()
                else:
                    _log(app, "Wallet: 'list' not in responseJSON3['result']", level="warning")
            except Exception as e:
                _log(app, f"Bybit wallet error: {e}", level="warning")

        # closed pnl per symbol
        all_symbols = sorted(all_symbols)
        _log(app, "Updating closed PnL from exchange")
        for symbol in all_symbols:
            trades = {}
            params = {"symbol": symbol, "category": "linear", "limit": 100}
            with create_connection(cfg.DATABASE) as conn:
                startTime = select_latest_income_symbol(conn, symbol)
                two_years_ago = datetime.now() - timedelta(days=729)
                two_years_ago_timestamp = int(two_years_ago.timestamp() * 1000)
                if startTime is None:
                    startTime = int(datetime.fromisoformat("2020-01-01 00:00:00+00:00").timestamp() * 1000)
                else:
                    startTime = int(startTime[0]) + 1
                if startTime < two_years_ago_timestamp:
                    startTime = two_years_ago_timestamp
                params["startTime"] = startTime

            if weightused > 50:
                _log(app, f"Weight used: {weightused}/100\nProcessed: {processed}\nSleep: 1 minute")
                sleeps += 1
                time.sleep(60)
                weightused = 0

            try:
                responseHeader4, responseJSON4 = send_signed_request(http_method="GET", url_path="/v5/position/closed-pnl", payload=params, exchange="bybit", app=app)
                if isinstance(responseJSON4, dict) and "rate_limit_status" in responseJSON4:
                    weightused = int(responseJSON4.get("rate_limit_status", weightused or 0))
                else:
                    weightused += 1

                if "result" in responseJSON4 and responseJSON4["result"] is not None and "list" in responseJSON4["result"]:
                    if responseJSON4["result"]["list"] is not None:
                        for trade in responseJSON4["result"]["list"]:
                            trades[trade.get("createdTime")] = [
                                trade.get("orderId"),
                                trade.get("execType"),
                                trade.get("closedPnl"),
                                trade.get("orderId"),
                            ]
                    else:
                        _log(app, "Closed PNL: responseJSON4['result']['list'] is None", level="warning")
                        break
                else:
                    _log(app, "Closed PNL: 'result' not found in responseJSON4", level="warning")
                    break
            except Exception as e:
                _log(app, f"Bybit closed PNL fetch error: {e}", level="warning")
                break

            if len(trades) > 0:
                trades = OrderedDict(sorted(trades.items()))
                with create_connection(cfg.DATABASE) as conn:
                    for trade in trades:
                        income_row = (
                            trades[trade][0],
                            symbol,
                            exec_type.get(trades[trade][1], trades[trade][1]),
                            trades[trade][2],
                            "USDT",
                            exec_type.get(trades[trade][1], trades[trade][1]),
                            int(trade),
                            trades[trade][0],
                        )
                        create_income(conn, income_row)
                        processed += 1
                    conn.commit()
    else:
        message = f"Exchange: {getattr(cfg, 'EXCHANGE', 'unknown')} is not currently supported"
        _log(app, message)

    elapsed = time.time() - start
    message = (
        f"Orders updated: {updated_orders}; Positions updated: {updated_positions} (new: {new_positions}); "
        f"Trades processed: {processed}; Time elapsed: {timedelta(seconds=elapsed)}; Sleeps: {sleeps}"
    )
    _log(app, message)

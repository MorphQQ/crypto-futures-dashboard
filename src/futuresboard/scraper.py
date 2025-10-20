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
import os  # Added for os.path.dirname (Pylance fix)
import requests  # type: ignore
from flask import Flask, current_app
import json
import asyncio
from queue import Queue, Empty  # For WS decoupling with timeout support
from flask_socketio import SocketIO  # For WS emit (import from app.py if registered)


from .config import Config  # Standalone load

q = Queue()  # Global queue for metrics emit (thread-safe with timeout)

emit_thread = None  # Global thread handle

def emit_worker():
    """Daemon thread: Poll queue and emit to WS clients."""
    from .app import socketio  # Lazy import
    from .db import logger  # Add for log
    print("Emit worker started", flush=True)
    while True:
        try:
            if socketio is None:
                print("WARNING: socketio None in emit_worker – retrying in 1s", flush=True)
                time.sleep(1)
                continue
            metrics = q.get(timeout=30)  # Block up to 30s
            if metrics:
                socketio.emit('metrics_update', {'data': metrics})  # Emit {'data': [3 pairs dicts]}
                print(f"Emitted metrics_update for {len(metrics)} pairs", flush=True)
                logger.info(f"Emitted {len(metrics)} batch via WS")  # Add for tail Select-String
            q.task_done()
        except Empty:
            pass  # No data, continue
        except Exception as e:
            print(f"Emit error: {e}", flush=True)
            logger.error(f"Emit error: {e}")  # Log err too
            time.sleep(1)  # Backoff

class HTTPRequestError(Exception):
    def __init__(self, url, code, msg=None):
        self.url = url
        self.code = code
        self.msg = msg

    def __str__(self) -> str:
        """
        Convert the exception into a printable string
        """
        return f"Request to {self.url!r} failed. Code: {self.code}; Message: {self.msg}"    


def auto_scrape(app):
    thread = threading.Thread(target=_auto_scrape, args=(app,))
    thread.daemon = True
    thread.start()


def _auto_scrape(app):
    global emit_thread
    if emit_thread is None:
        emit_thread = threading.Thread(target=emit_worker, daemon=True)
        emit_thread.start()
        app.logger.info("Emit thread started")
    
    interval = app.config["AUTO_SCRAPE_INTERVAL"]
    tfs = ['5m', '15m', '30m', '1h']  # P2 cycle
    tf_idx = 0
    while True:
        app.logger.info("Auto scrape routines starting")
        tf = tfs[tf_idx % len(tfs)]  # Rotate tf
        app.logger.info("Auto scrape routines starting tf=" + tf)  # Test log (tf rotate)
        # ... existing scrape(app=app) ...
        # Lazy metrics hook: Pass tf to get_all_metrics/save_metrics
        from .metrics import get_all_metrics
        from .db import save_metrics
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        metrics = loop.run_until_complete(get_all_metrics(tf=tf))  # Add tf
        saved_count = save_metrics(metrics, tf)  # tf
        app.logger.info(f"Saved {saved_count} metrics tf={tf}")  # Test saved
        q.put(metrics)  # Emit tf-specific
        app.logger.info(f"Queued {len(metrics)} for WS emit tf={tf}")  # Test queue
        loop.close()
        tf_idx += 1
        time.sleep(interval)


def hashing(query_string, exchange="binance", timestamp=None):
    cfg_path = pathlib.Path(os.path.dirname(__file__)).parent.parent
    cfg = Config.from_config_dir(cfg_path)
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


def dispatch_request(http_method, signature=None, timestamp=None, app=None):  # Add app=None for standalone
    session = requests.Session()
    # Standalone config (no current_app)
    cfg_path = pathlib.Path(os.path.dirname(__file__)).parent.parent
    cfg = Config.from_config_dir(cfg_path)
    api_key = cfg.API_KEY
    
    session.headers.update(
        {
            "Content-Type": "application/json;charset=utf-8",
            "X-MBX-APIKEY": api_key,
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-SIGN": f"{signature}",
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": f"{timestamp}",
            "X-BAPI-RECV-WINDOW": "5000",
        }
    )

    return {
        "GET": session.get,
        "DELETE": session.delete,
        "PUT": session.put,
        "POST": session.post,
    }.get(http_method, "GET")


# used for sending request requires the signature
def send_signed_request(http_method, url_path, payload={}, exchange="binance", app=None):
    cfg_path = pathlib.Path(os.path.dirname(__file__)).parent.parent
    cfg = Config.from_config_dir(cfg_path)
    if exchange == "binance":
        payload["timestamp"] = get_timestamp()
    query_string = urlencode(OrderedDict(sorted(payload.items())))
    query_string = query_string.replace(
        "%27", "%22"
    )  # replace single quote to double quote

    url = f"{cfg.API_BASE_URL}{url_path}?{query_string}"
    if exchange == "binance":
        url += f"&signature={hashing(query_string, exchange)}"

    params = {"url": url, "params": {}}
    try:
        timestamp = get_timestamp()
        response = dispatch_request(
            http_method,
            hashing(query_string=query_string, exchange=exchange, timestamp=timestamp),
            timestamp=timestamp,
            app=app
        )(**params)
        headers = response.headers
        try:
            json_response = response.json()
        except requests.exceptions.JSONDecodeError as e:
            raise HTTPRequestError(url=url, code=-3, msg=f"{e}")
        if "code" in json_response:
            raise HTTPRequestError(
                url=url, code=json_response["code"], msg=json_response["msg"]
            )
        if "retCode" in json_response:
            if json_response["retCode"] != 0:
                raise HTTPRequestError(
                    url=url, code=json_response["retCode"], msg=json_response["retMsg"]
                )
        return headers, json_response
    except requests.exceptions.ConnectionError as e:
        raise HTTPRequestError(url=url, code=-1, msg=f"{e}")


# used for sending public data request
def send_public_request(url_path, payload={}, app=None):  # Add app=None
    query_string = urlencode(payload, True)
    # Standalone config (no current_app)
    cfg_path = pathlib.Path(os.path.dirname(__file__)).parent.parent
    cfg = Config.from_config_dir(cfg_path)
    api_base_url = cfg.API_BASE_URL
    url = api_base_url + url_path
    if query_string:
        url = url + "?" + query_string
    try:
        response = dispatch_request("GET", app=app)(url=url)  # Pass app
        headers = response.headers
        try:
            json_response = response.json()
        except requests.exceptions.JSONDecodeError as e:
            raise HTTPRequestError(url=url, code=-3, msg=f"{e}")
        if "code" in json_response:
            raise HTTPRequestError(
                url=url, code=json_response["code"], msg=json_response["msg"]
            )
        return headers, json_response
    except requests.exceptions.ConnectionError as e:
        raise HTTPRequestError(url=url, code=-2, msg=f"{e}")


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


def db_setup(database, app=None):  # Fix: Add app param for context
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
    
    # create a database connection (raw for non-Flask calls)
    conn = create_connection(database)

    # create tables
    if conn is not None:
        create_table(conn, sql_create_income_table)
        create_table(conn, sql_create_position_table)
        create_table(conn, sql_create_account_table)
        create_table(conn, sql_create_orders_table)
        conn.close()
    else:
        print("Error! cannot create the database connection.")
    
    # Metrics table: Lazy import + raw create (context-wrapped if app)
    try:
        from .db import create_metrics_table
        if app:  # Fix: Use passed app for context (thread-safe)
            with app.app_context():  # Push context for query/get_db/current_app
                create_metrics_table()
            print("Metrics table created/verified OK")  # Debug success
        else:
            raise ImportError("No app for context – fallback to raw")
    except ImportError:
        print("Metrics DB support missing—install/update db.py")
    except Exception as e:
        print(f"Metrics table setup error: {e}")
        # Fallback: Raw SQL CREATE if SQLAlchemy fails (aligns FinalRoadmap cols)
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
                print("Metrics table created via raw SQL fallback")
            except sqlite3.Error as se:
                print(f"Raw SQL error: {se}")
            finally:
                conn.close()


# income interactions
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


# position interactions
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


# account interactions
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


# orders interactions
def delete_all_orders(conn):
    sql = """ DELETE FROM orders """
    cur = conn.cursor()
    cur.execute(sql)
    conn.commit()


def create_orders(conn, orders):
    sql = """ INSERT INTO orders(origQty, price, side, positionSide, status, symbol, time, type) VALUES(?,?,?,?,?,?,?,?) """
    cur = conn.cursor()
    cur.execute(sql, orders)


def scrape(app=None):
    try:
        _scrape(app=app)
    except HTTPRequestError as exc:
        if app is None:
            print(exc)
        else:
            app.logger.error(f"{exc}")
    except Exception as e:
        if app is None:
            print(f"Scrape error: {e}")
        else:
            app.logger.error(f"Scrape error: {e}")
    
    # Lazy metrics hook post-scrape (standalone, no temp app)
    try:
        from .metrics import get_all_metrics
        from .db import save_metrics
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        metrics = loop.run_until_complete(get_all_metrics())
        saved_count = save_metrics(metrics)
        loop.close()
        if app is None:
            print(f"Saved {saved_count} metrics (CLI mode)")
        else:
            app.logger.info(f"Saved {saved_count} metrics")
        q.put(metrics)  # Queue for WS emit (if app)
    except ImportError as ie:
        if app is None:
            print(f"Metrics import failed (CLI): {ie}")
        else:
            app.logger.warning(f"Metrics import failed: {ie}")
    except Exception as m_e:
        if app is None:
            print(f"Metrics error (CLI): {m_e}")
        else:
            app.logger.error(f"Metrics error: {m_e}")


# flake8: noqa: C901
def _scrape(app=None):
    start = time.time()
    cfg_path = pathlib.Path(os.path.dirname(__file__)).parent.parent
    cfg = Config.from_config_dir(cfg_path)
    db_setup(cfg.DATABASE, app)  # Fix: Pass app for context

    up_to_date = False
    weightused = 0
    processed, updated_positions, new_positions, updated_orders, sleeps = 0, 0, 0, 0, 0

    exchange_str = str(cfg.EXCHANGE).lower()
    if 'binance' in exchange_str:
        if weightused < 800:
            responseHeader, responseJSON = send_signed_request(
                "GET", "/fapi/v1/openOrders", app=app
            )
            weightused = int(responseHeader["X-MBX-USED-WEIGHT-1M"])

            with create_connection(cfg.DATABASE) as conn:
                delete_all_orders(conn)
                for order in responseJSON:
                    updated_orders += 1
                    row = (
                        float(order["origQty"]),
                        float(order["price"]),
                        order["side"],
                        order["positionSide"],
                        order["status"],
                        order["symbol"],
                        int(order["time"]),
                        order["type"],
                    )
                    create_orders(conn, row)
                conn.commit()

        responseHeader, responseJSON = send_signed_request("GET", "/fapi/v2/account", app=app)
        weightused = int(responseHeader["X-MBX-USED-WEIGHT-1M"])

        overweight = False
        try:
            positions = responseJSON["positions"]
        except Exception:
            overweight = True

        if not overweight:
            with create_connection(cfg.DATABASE) as conn:
                totals_row = (
                    float(responseJSON["totalWalletBalance"]),
                    float(responseJSON["totalUnrealizedProfit"]),
                    float(responseJSON["totalMarginBalance"]),
                    float(responseJSON["availableBalance"]),
                    float(responseJSON["maxWithdrawAmount"]),
                    1,
                )
                accountCheck = select_account(conn)
                if accountCheck is None:
                    create_account(conn, totals_row)
                elif float(accountCheck[0]) != float(
                    responseJSON["totalWalletBalance"]
                ):
                    update_account(conn, totals_row)

                delete_all_positions(conn)

                for position in positions:
                    position_row = (
                        float(position["unrealizedProfit"]),
                        int(position["leverage"]),
                        float(position["entryPrice"]),
                        float(position["positionAmt"]),
                        position["symbol"],
                        position["positionSide"],
                    )

                    create_position(conn, position_row)
                    updated_positions += 1

                conn.commit()

        while not up_to_date:
            if weightused > 800:
                message = f"Weight used: {weightused}/800\nProcessed: {processed}\nSleep: 1 minute"
                if app is None:
                    print(message)
                else:
                    app.logger.info(message)
                sleeps += 1
                time.sleep(60)

            with create_connection(cfg.DATABASE) as conn:
                startTime = select_latest_income(conn)
                if startTime is None:
                    startTime = int(
                        datetime.fromisoformat("2020-01-01 00:00:00+00:00").timestamp()
                        * 1000
                    )
                else:
                    startTime = startTime[0]

                params = {"startTime": startTime + 1, "limit": 1000}

                responseHeader, responseJSON = send_signed_request(
                    http_method="GET", url_path="/fapi/v1/income", payload=params, app=app
                )
                weightused = int(responseHeader["X-MBX-USED-WEIGHT-1M"])

                if len(responseJSON) == 0:
                    up_to_date = True
                else:
                    for income in responseJSON:
                        if len(income["tradeId"]) == 0:
                            income["tradeId"] = 0
                        income_row = (
                            int(income["tranId"]),
                            income["symbol"],
                            income["incomeType"],
                            income["income"],
                            income["asset"],
                            income["info"],
                            int(income["time"]),
                            int(income["tradeId"]),
                        )
                        create_income(conn, income_row)
                        processed += 1

                    conn.commit()
    elif 'bybit' in exchange_str:
        all_symbols = []
        exec_type = {
            "Trade": "REALIZED_PNL",
            "Funding": "FUNDING_FEE",
            "AdlTrade": "ADLTRADE",
            "BustTrade": "BUSTTRADE",
        }

        params = {"category": "linear", "limit": 200, "settleCoin": "USDT"}
        responseHeader, responseJSON = send_signed_request(
            http_method="GET",
            url_path="/v5/position/list",
            payload=params,
            exchange="bybit",
            app=app,
        )
        if "rate_limit_status" in responseJSON:
            weightused = int(responseJSON["rate_limit_status"])

        with create_connection(cfg.DATABASE) as conn:
            message = "Deleting orders and positions from db"
            if app is None:
                print(message)
            else:
                app.logger.info(message)
            delete_all_orders(conn)
            delete_all_positions(conn)
            message = "Loading orders and positions from exchange"
            if app is None:
                print(message)
            else:
                app.logger.info(message)
            if "result" in responseJSON:
                if "list" in responseJSON["result"]:
                    for position in responseJSON["result"]["list"]:
                        if weightused > 50:
                            message = f"Weight used: {weightused}/{120-weightused}\nProcessed: {updated_positions + new_positions + updated_orders}\nSleep: 1 minute"
                            if app is None:
                                print(message)
                            else:
                                app.logger.info(message)
                            sleeps += 1
                            time.sleep(60)
                            weightused = 0

                        if position["symbol"] not in all_symbols:
                            all_symbols.append(position["symbol"])

                        if float(position["size"]) > 0:
                            position_sides = {"buy": "LONG", "sell": "SHORT"}
                            positionside = position_sides[position["side"].lower()]

                            position_row = (
                                float(position["unrealisedPnl"]),
                                int(position["leverage"]),
                                float(position["avgPrice"]),
                                float(position["size"]),
                                position["symbol"],
                                positionside,
                            )

                            create_position(conn, position_row)
                            updated_positions += 1

                            params = {
                                "symbol": position["symbol"],
                                "category": "linear",
                            }
                            responseHeader, responseJSON = send_signed_request(
                                http_method="GET",
                                url_path="/v5/order/realtime",
                                payload=params,
                                exchange="bybit",
                                app=app,
                            )
                            if "rate_limit_status" in responseJSON:
                                weightused = int(responseJSON["rate_limit_status"])
                            else:
                                weightused += 1

                            if "result" in responseJSON:
                                if "list" in responseJSON["result"]:
                                    for order in responseJSON["result"]["list"]:
                                        updated_orders += 1

                                        orderside = order["side"].upper()

                                        row = (
                                            float(order["qty"]),
                                            float(order["price"]),
                                            orderside,
                                            positionside,
                                            order["orderStatus"],
                                            order["symbol"],
                                            int(order["createdTime"]),
                                            order["orderType"],
                                        )
                                        create_orders(conn, row)
                                else:
                                    message = "Orders: 'list' not in responseJSON['result']"
                                    if app is None:
                                        print(message)
                                    else:
                                        app.logger.warning(message)
                            else:
                                message = "Orders: 'result' not in responseJSON"
                                if app is None:
                                    print(message)
                                else:
                                    app.logger.warning(message)
                else:
                    message = "Positions: 'list' not in responseJSON['result']"
                    if app is None:
                        print(message)
                    else:
                        app.logger.warning(message)
            else:
                message = "Positions: 'result' not in responseJSON"
                if app is None:
                    print(message)
                else:
                    app.logger.warning(message)

            params = {"coin": "USDT", "accountType": "CONTRACT"}
            responseHeader, responseJSON = send_signed_request(
                http_method="GET",
                url_path="/v5/account/wallet-balance",
                payload=params,
                exchange="bybit",
                app=app,
            )
            message = "Updating wallet balance from exchange"
            if app is None:
                print(message)
            else:
                app.logger.info(message)
            if "result" in responseJSON:
                if "list" in responseJSON["result"]:
                    if (
                        responseJSON["result"]["list"][0]["totalMaintenanceMargin"]
                        == ""
                    ):
                        maintenance_margin = 0.0
                    else:
                        maintenance_margin = float(
                            responseJSON["result"]["list"][0]["totalMaintenanceMargin"]
                        )

                    totals_row = (
                        float(
                            responseJSON["result"]["list"][0]["coin"][0][
                                "walletBalance"
                            ]
                        ),
                        float(
                            responseJSON["result"]["list"][0]["coin"][0][
                                "unrealisedPnl"
                            ]
                        ),
                        maintenance_margin,
                        float(
                            responseJSON["result"]["list"][0]["coin"][0][
                                "availableToWithdraw"
                            ]
                        ),
                        float(0),
                        1,
                    )

                    accountCheck = select_account(conn)
                    if accountCheck is None:
                        create_account(conn, totals_row)
                    elif float(accountCheck[0]) != float(
                        responseJSON["result"]["list"][0]["coin"][0]["walletBalance"]
                    ):
                        update_account(conn, totals_row)

                    conn.commit()
                else:
                    message = "Wallet: 'list' not in responseJSON['result']"
                    if app is None:
                        print(message)
                    else:
                        app.logger.warning(message)
            else:
                message = "Wallet: 'result' not in responseJSON"
                if app is None:
                    print(message)
                else:
                    app.logger.warning(message)

        all_symbols = sorted(all_symbols)
        message = "Updating closed PnL from exchange"
        if app is None:
            print(message)
        else:
            app.logger.info(message)
        for symbol in all_symbols:
            trades = {}
            params = {
                "symbol": symbol,
                "category": "linear",
                "limit": 100,
            }
            with create_connection(cfg.DATABASE) as conn:
                startTime = select_latest_income_symbol(conn, symbol)
                two_years_ago = datetime.now() - timedelta(days=729)
                two_years_ago_timestamp = int(two_years_ago.timestamp() * 1000)
                if startTime is None:
                    startTime = int(
                        datetime.fromisoformat("2020-01-01 00:00:00+00:00").timestamp()
                        * 1000
                    )
                else:
                    startTime = int(startTime[0]) + 1

                if startTime < two_years_ago_timestamp:
                    startTime = two_years_ago_timestamp
                params["startTime"] = startTime

            if weightused > 50:
                message = f"Weight used: {weightused}/100\nProcessed: {processed}\nSleep: 1 minute"
                if app is None:
                    print(message)
                else:
                    app.logger.info(message)
                sleeps += 1
                time.sleep(60)
                weightused = 0

            responseHeader, responseJSON = send_signed_request(
                http_method="GET",
                url_path="/v5/position/closed-pnl",
                payload=params,
                exchange="bybit",
                app=app,
            )

            if "rate_limit_status" in responseJSON:
                weightused = int(responseJSON["rate_limit_status"])
            else:
                weightused += 1

            if "result" in responseJSON:
                if responseJSON["result"] is not None:
                    if "list" in responseJSON["result"]:
                        if responseJSON["result"]["list"] is not None:
                            for trade in responseJSON["result"]["list"]:
                                trades[trade["createdTime"]] = [
                                    trade["orderId"],
                                    trade["execType"],
                                    trade["closedPnl"],
                                    trade["orderId"],
                                ]

                        else:
                            message = "Closed PNL: responseJSON['result']['list'] is None"
                            if app is None:
                                print(message)
                            else:
                                app.logger.warning(message)
                            break
                    else:
                        message = "Closed PNL: 'data' not found in responseJSON['result']['list']"
                        if app is None:
                            print(message)
                        else:
                            app.logger.warning(message)
                        break
                else:
                    message = "Closed PNL: 'result' is None"
                    if app is None:
                        print(message)
                    else:
                        app.logger.warning(message)
                    break
            else:
                message = "Closed PNL: 'result' not found in responseJSON"
                if app is None:
                    print(message)
                else:
                    app.logger.warning(message)
                break

            if len(trades) > 0:
                trades = OrderedDict(sorted(trades.items()))
                with create_connection(cfg.DATABASE) as conn:
                    for trade in trades:
                        income_row = (
                            trades[trade][0],
                            symbol,
                            exec_type[trades[trade][1]],
                            trades[trade][2],
                            "USDT",
                            exec_type[trades[trade][1]],
                            int(trade),
                            trades[trade][0],
                        )

                        create_income(conn, income_row)
                        processed += 1
                    conn.commit()
    else:
        message = f"Exchange: {cfg.EXCHANGE} is not currently supported"
        if app is None:
            print(message)
        else:
            app.logger.info(message)

    elapsed = time.time() - start
    message = f"Orders updated: {updated_orders}; Positions updated: {updated_positions} (new: {new_positions}); Trades processed: {processed}; Time elapsed: {timedelta(seconds=elapsed)}; Sleeps: {sleeps}"
    if app is None:
        print(message)
    else:
        app.logger.info(message)
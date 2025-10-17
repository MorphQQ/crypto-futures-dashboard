from __future__ import annotations

import json
import logging
import pathlib
import sqlite3
from logging.handlers import RotatingFileHandler

import argparse
import os
from dotenv import load_dotenv  # .env load (API_KEY, AUTO_SCRAPE_INTERVAL)

from flask import Flask, redirect, request, render_template, current_app, jsonify  # Added jsonify
from flask_cors import CORS  # For frontend fetches
from flask_socketio import SocketIO  # WS for Phase 1 refreshes

from futuresboard import blueprint
from futuresboard import db
from futuresboard.config import Config
from .db import get_latest_metrics, get_metrics_by_symbol  # For history routes (moved early, no cycle)

# Logging setup (Phase 1: app.log 10MB x3 rotate)
log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')  # backend/logs
os.makedirs(log_dir, exist_ok=True)
file_handler = RotatingFileHandler(os.path.join(log_dir, 'app.log'), maxBytes=10*1024*1024, backupCount=3)
file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'))
file_handler.setLevel(logging.INFO)
logger = logging.getLogger(__name__)
logger.addHandler(file_handler)
logger.setLevel(logging.INFO)

# Redirect print to log (optional; uncomment)
# def print(*args, **kwargs): logger.info(' '.join(map(str, args)))

print("Logging setup complete - check backend/logs/app.log")  # Test entry

socketio = None  # Module-level export for scraper import (set in init_app)


def clear_trailing():
    rp = request.path 
    if rp != "/" and rp.endswith("/") and not rp.startswith('/socket.io'):
        return redirect(rp[:-1])


def init_app(config: Config | None = None):
    global socketio  # Set module-level
    if config is None:
        config = Config.from_config_dir(pathlib.Path.cwd())

    app = Flask(__name__)
    
    # Config load: Handle path str or Config object
    if isinstance(config, str):  # Raw path str
        with open(config, 'r') as f:
            config_data = json.load(f)
        app.config.from_mapping(**config_data)
    elif hasattr(config, 'path'):  # Config with path attr
        with open(config.path, 'r') as f:
            config_data = json.load(f)
        app.config.from_mapping(**config_data)
    else:  # Config object
        app.config.from_object(config)
    
    # Ensure defaults if missing (post-load)
    if 'symbols' not in app.config:
        app.config['symbols'] = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
    if 'API_BASE_URL' not in app.config:
        app.config['API_BASE_URL'] = 'https://fapi.binance.com'
    app.config['sandbox'] = app.config.get('TEST_MODE', False)  # Map: false=live, true=sandbox
    
    app.url_map.strict_slashes = False
    db.init_app(app)
    app.before_request(clear_trailing)
    app.register_blueprint(blueprint.app)
    
    # Add CORS early for all routes
    CORS(app, origins=['http://localhost:5173'])  # Frontend allowed

    # New: History routes (for charts/frontend) - early, no cycle
    @app.route('/api/metrics/history')
    def api_metrics_history():
        limit = request.args.get('limit', 50, type=int)
        data = get_latest_metrics(limit)
        return jsonify([dict(row) for row in data])  # Row to dict for JSON

    @app.route('/api/metrics/<symbol>/history')
    def api_symbol_history(symbol):
        limit = request.args.get('limit', 24, type=int)  # Default hourly
        data = get_metrics_by_symbol(symbol, limit)
        return jsonify([dict(row) for row in data])

    @app.route('/health', methods=['GET'])
    def health_check():
        #print("Health route registered at /health")  # Debug: Confirms def executes
        try:
            # DB ping via config (aligns pydantic resolve config/futures.db)
            from .config import Config
            cfg = Config.from_config_dir(pathlib.Path.cwd())
            db_path = str(cfg.DATABASE)
            conn = sqlite3.connect(db_path)
            conn.execute('SELECT 1')
            conn.close()
            return jsonify({'status': 'healthy', 'version': 'v0.3.3', 'db_path': db_path}), 200
        except Exception as e:
            current_app.logger.error(f"Health check failed: {e}")
            return jsonify({'status': 'unhealthy', 'error': str(e)}), 500
        
    # Lazy metrics import + route add (breaks cycle: post-app init)
    from .metrics import add_metrics_route, metrics_bp  # Lazy: Import here + metrics_bp for register
    add_metrics_route(app)

    # Register metrics_bp with prefix (for /api/metrics, /api/health, /api/<symbol>/history)
    app.register_blueprint(metrics_bp, url_prefix='/api')  # Prefix /api (metrics_bp routes '/' → /api/metrics)

    # Init SocketIO (Phase 1 WS for metrics_update)
    socketio = SocketIO(app, cors_allowed_origins="*", logger=True, engineio_logger=True)  # * for dev; restrict prod

    # Lazy scraper import + auto_scrape (breaks cycle: after app setup)
    if not config.DISABLE_AUTO_SCRAPE:
        import futuresboard.scraper  # Lazy: Import here (post-app init)
        futuresboard.scraper.auto_scrape(app)

    app.logger.setLevel(logging.INFO)
    #print(app.url_map)  # Debug: Show all routes (expect /health GET)
    return app

def main():
    load_dotenv()  # Load .env early (keys for CCXT/metrics.py)
    
    parser = argparse.ArgumentParser(description='Crypto Futures Dashboard (Modified v0.3.3)')
    parser.add_argument('--port', type=int, default=5000, help='Port to run on (default: 5000)')
    args = parser.parse_args()
    
    # Init app (uses config.json + .env overrides)
    print("Initializing app...")  # Debug: Aligns "Importing..." from init_app
    app = init_app()  # Calls existing init_app (bp register, scraper auto)
    
    # Health/debug post-init (roadmap: /health route via metrics_bp)
    print(f"metrics_bp imported successfully (pairs: {app.config.get('symbols', [])})")
    print(f"Starting SocketIO on http://0.0.0.0:{args.port} (debug mode)...")
    
    # Run (SocketIO handles WS/HTTP; allow_unsafe_werkzeug for Windows/debug)
    socketio.run(app, host='0.0.0.0', port=args.port, debug=True, allow_unsafe_werkzeug=True)

if __name__ == '__main__':
    main()
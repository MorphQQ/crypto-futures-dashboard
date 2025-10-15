from __future__ import annotations

import json
import logging
import pathlib

from flask import Flask, redirect, request, render_template, current_app, jsonify  # Added jsonify
from flask_cors import CORS  # For frontend fetches

import futuresboard.scraper
from futuresboard import blueprint
from futuresboard import db
from futuresboard.config import Config
from .metrics import add_metrics_route  # Our metrics API
from .db import get_latest_metrics, get_metrics_by_symbol  # For history routes


def clear_trailing():
    rp = request.path
    if rp != "/" and rp.endswith("/"):
        return redirect(rp[:-1])


def init_app(config: Config | None = None):
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
    CORS(app)

    # Fixed: Single call, post-setup, with raw 'app' (no current_app)
    add_metrics_route(app)

    # New: History routes (for charts/frontend)
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

    if not config.DISABLE_AUTO_SCRAPE:  # Bool check (assuming Config has attr)
        futuresboard.scraper.auto_scrape(app)

    app.logger.setLevel(logging.INFO)
    
    return app
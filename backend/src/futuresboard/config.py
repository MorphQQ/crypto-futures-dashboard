"""
Configuration module for the Crypto Futures Quant Platform.
✅ Supports Pydantic v2 + pydantic-settings
✅ Handles comma-separated or JSON SYMBOLS
✅ Hot-reloadable without restarting backend
"""

from __future__ import annotations
from typing import List, Union
from functools import lru_cache
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="backend/.env",
        env_file_encoding="utf-8",
        extra="allow"  # allows continuity + vite_* + dev flags
    )
    # ===============================================================
    # 🗄️ DATABASE
    # ===============================================================
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/futures"
    DB_POOL_MIN: int = 1
    DB_POOL_MAX: int = 10

    # ===============================================================
    # ⚙️ RUNTIME
    # ===============================================================
    # Accepts either a string ("BTCUSDT,ETHUSDT,SOLUSDT")
    # or a list (["BTCUSDT","ETHUSDT","SOLUSDT"])
    SYMBOLS: Union[str, List[str]] = "BTCUSDT,ETHUSDT,SOLUSDT"
    AUTO_SCRAPE_INTERVAL: int = 10
    REST_CONCURRENCY: int = 10
    MAX_STREAMS_PER_CONN: int = 50
    LOG_LEVEL: str = "INFO"

    # ===============================================================
    # 📊 META / CONTEXT
    # ===============================================================
    PHASE: str = "P4.4 - Backend Continuity & Optimization Audit"
    API_BASE_URL: str = "https://fapi.binance.com"

    # ===============================================================
    # 🧠 VALIDATORS
    # ===============================================================
    @field_validator("SYMBOLS", mode="before")
    @classmethod
    def normalize_symbols(cls, v):
        """
        Normalize SYMBOLS from env or code.
        Handles:
        - "BTCUSDT,ETHUSDT,SOLUSDT"
        - ["BTCUSDT","ETHUSDT","SOLUSDT"]
        """
        if isinstance(v, str):
            return [s.strip().upper() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            return [str(s).strip().upper() for s in v if s]
        return ["BTCUSDT", "ETHUSDT", "SOLUSDT"]



# ===============================================================
# 🔁 HOT-RELOAD HELPERS
# ===============================================================
@lru_cache
def get_settings() -> Settings:
    """
    Cached singleton accessor — used by all backend modules.
    """
    return Settings()


def reload_settings() -> Settings:
    """
    Hot-reload .env settings without restarting the backend.
    Detects .env path automatically and updates os.environ.
    """
    import os
    from dotenv import dotenv_values

    # 🔍 Detect correct .env path (backend/.env or project root)
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    candidate_paths = [
        os.path.join(repo_root, ".env"),
        os.path.join(repo_root, "backend", ".env"),
    ]
    env_path = next((p for p in candidate_paths if os.path.exists(p)), None)

    print(f"[config] 🔍 Using env path: {env_path}")

    # 1️⃣ Re-read .env and update environment variables
    if env_path:
        env_data = dotenv_values(env_path)
        for k, v in env_data.items():
            if v is not None:
                os.environ[k] = v

    # 2️⃣ Clear cached instance
    get_settings.cache_clear()

    # 3️⃣ Rebuild and return new config
    new_settings = Settings()
    print(f"[config] 🔁 Settings reloaded (log_level={new_settings.LOG_LEVEL}, symbols={new_settings.SYMBOLS})")
    return new_settings



from __future__ import annotations

import copy
import enum
import json
import pathlib
from typing import List
from typing import Optional

from pydantic import BaseModel
from pydantic import DirectoryPath
from pydantic import Field
from pydantic import root_validator
from pydantic import validator

from dotenv import load_dotenv
import os

load_dotenv()  # Pulls .env

class NavbarBG(enum.Enum):
    BG_DARK = "bg-dark"
    BG_PRIMARY = "bg-primary"
    BG_SECONDARY = "bg-secondary"
    BG_SUCCESS = "bg-success"
    BG_DANGER = "bg-danger"
    BG_WARNING = "bg-warning"
    BG_INFO = "bg-info"
    BG_LIGHT = "bg-light"


class Exchanges(enum.Enum):
    BINANCE = "binance"
    BYBIT = "bybit"


class Custom(BaseModel):
    NAVBAR_TITLE: Optional[str] = Field("Futuresboard", min_length=1, max_length=50)
    NAVBAR_BG: Optional[NavbarBG] = NavbarBG.BG_DARK
    PROJECTIONS: List[float] = Field([1.003, 1.005, 1.01, 1.012], min_items=1, max_items=10)

    @validator("PROJECTIONS", each_item=True)
    @classmethod
    def _validate_projections(cls, value):
        if not isinstance(value, float):
            try:
                value = float(value)
            except TypeError:
                raise ValueError(f"Cannot cast {value!r} to a float")
        if value < -3.0:
            raise ValueError("The lower allowed projection value is -3.0")
        if value > 3.0:
            raise ValueError("The upper allowed projection value is 3.0")
        return value


class Config(BaseModel):
    CONFIG_DIR: DirectoryPath = pathlib.Path.cwd()
    DATABASE: Optional[pathlib.Path]
    EXCHANGE: Optional[Exchanges] = Exchanges.BINANCE
    TEST_MODE: Optional[bool] = False
    API_BASE_URL: Optional[str]
    AUTO_SCRAPE_INTERVAL: int = 300
    DISABLE_AUTO_SCRAPE: bool = False
    HOST: Optional[str] = Field(default='0.0.0.0')  # Fix: Str default (v1 compat; no IPvAnyInterface call)
    PORT: Optional[int] = Field(5000, ge=1, le=65535)
    API_KEY: str
    API_SECRET: str
    SYMBOLS: List[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "SOLUSDT"])  # New: Uppercase, default list (never None)
    CUSTOM: Optional[Custom] = Custom()

    @validator("DATABASE", always=True)
    @classmethod
    def _validate_database(cls, value, values):
        if not value:
            value = values["CONFIG_DIR"] / "futures.db"
        return value.resolve()

    @validator("API_BASE_URL", always=True)
    @classmethod
    def _validate_api_base_url(cls, value, values):
        if not value:
            if values["EXCHANGE"] == Exchanges.BINANCE:
                if values["TEST_MODE"]:
                    value = "https://testnet.binancefuture.com"
                else:
                    value = "https://fapi.binance.com"
            elif values["EXCHANGE"] == Exchanges.BYBIT:
                if values["TEST_MODE"]:
                    value = "https://api-testnet.bybit.com"
                else:
                    value = "https://api.bybit.com"
        return value

    @validator("AUTO_SCRAPE_INTERVAL")
    @classmethod
    def _validate_interval(cls, value):
        if value < 10:  # Lower for Phase 1 WS (<30s); CCXT rate-safe
            raise ValueError("The lower allowed value is 10")
        if value > 3600:
            raise ValueError("The upper allowed value is 3600")
        return value

    @root_validator(pre=True)
    @classmethod
    def _capitalize_all_keys(cls, fields):
        def _capitalize_keys(c):
            if not isinstance(c, dict):
                return c
            for key, value in copy.deepcopy(c).items():
                if isinstance(value, dict):
                    _capitalize_keys(value)
                c[key.upper()] = value
                if not key.isupper():
                    c.pop(key)

        for key, value in copy.deepcopy(fields).items():
            _capitalize_keys(value)
            fields[key.upper()] = value
            if not key.isupper():
                fields.pop(key)

        return fields

    @classmethod
    def from_config_dir(cls, config_dir: pathlib.Path) -> Config:
        config_file = config_dir / "config.json"
        if config_file.exists():
            config_dict = json.loads(config_file.read_text())
        else:
            config_dict = {}
        config_dict["CONFIG_DIR"] = config_dir

        # .env Overrides (keep existing)
        env_exchange = os.getenv("EXCHANGE", config_dict.get("EXCHANGE", "binance")).upper()
        config_dict["EXCHANGE"] = Exchanges[env_exchange]

        config_dict["API_KEY"] = os.getenv("API_KEY", config_dict.get("API_KEY", ""))
        config_dict["API_SECRET"] = os.getenv("API_SECRET", config_dict.get("API_SECRET", ""))
        config_dict["AUTO_SCRAPE_INTERVAL"] = int(os.getenv("INTERVAL", str(config_dict.get("AUTO_SCRAPE_INTERVAL", 300))))

        config_dict["TEST_MODE"] = os.getenv("TEST_MODE", "False").lower() == "true"
        
        # New: SYMBOLS from .env/JSON (comma-split if env str)
        env_symbols = os.getenv("SYMBOLS", "")
        if env_symbols:
            config_dict["SYMBOLS"] = [s.strip() for s in env_symbols.split(",") if s.strip()]
        elif "SYMBOLS" not in config_dict:
            config_dict["SYMBOLS"] = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]  # Ensure list

        # Debug: Print/raise post-override (commented out to suppress spam)
        # print(f"Debug Config: API_KEY len={len(config_dict.get('API_KEY', '')) if config_dict.get('API_KEY') else 'MISSING'}; SECRET loaded={bool(config_dict.get('API_SECRET'))}")
        # if not config_dict.get("API_KEY") or config_dict["API_KEY"] == "":
        #     raise ValueError("API_KEY empty/missing after .env/JSON override—check .env (API_KEY=HQ9v...) or config.json fallback")

        return cls.parse_obj(config_dict)
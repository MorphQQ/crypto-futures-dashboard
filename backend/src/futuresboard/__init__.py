# backend/src/futuresboard/__init__.py
# Early async detection + package metadata
from __future__ import annotations
import pathlib

# Package init: do not monkey-patch eventlet/gevent.
# Prefer asyncio as canonical async runtime for this app.
_ASYNC_MODE = "asyncio"

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent

try:
    from .version import __version__
except ImportError:  # pragma: no cover
    __version__ = "0.0.0.not-installed"
    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            __version__ = version("futuresboard")
        except PackageNotFoundError:
            pass
    except ImportError:
        try:
            from pkg_resources import get_distribution, DistributionNotFound  # type: ignore
            try:
                __version__ = get_distribution("futuresboard").version
            except DistributionNotFound:
                pass
        except ImportError:
            pass

__all__ = ["_ASYNC_MODE", "__version__"]
__all__.extend(["config", "db", "models", "scraper", "server", "utils"])   # submodules
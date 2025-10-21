# diag_init.py - diagnostic import/run helper for futuresboard
import sys
import traceback
import pathlib
import os

# Ensure we can print unicode on Windows consoles
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE  # script expected at repo root; adjust if different
BACKEND_SRC = REPO_ROOT / "backend" / "src"

print("Repository root:", REPO_ROOT)
print("Adding to sys.path:", str(BACKEND_SRC))
# Put backend/src first so package-relative imports succeed
sys.path.insert(0, str(BACKEND_SRC))
# Also add repo root in case some imports expect it
sys.path.insert(0, str(REPO_ROOT))

def try_import(name, alias=None):
    try:
        m = __import__(name, fromlist=["*"])
        print(f"[OK] Imported {name}")
        return m
    except Exception:
        print(f"[FAIL] Importing {name} raised:")
        traceback.print_exc()
        return None

print("\n== Attempting imports ==")
# Try package imports by absolute name as the app expects (futuresboard.*)
metrics_mod = try_import("futuresboard.metrics")
db_mod = try_import("futuresboard.db")
app_mod = try_import("futuresboard.app")

print("\n== Inspecting db path and simple DB check ==")
if db_mod:
    try:
        db_path = getattr(db_mod, "DB_PATH", None)
        print("db_mod.DB_PATH:", db_path)
        # try a quick get_latest_metrics if available
        if hasattr(db_mod, "get_latest_metrics"):
            try:
                rows = db_mod.get_latest_metrics(limit=1, db_path=db_path or "backend/src/futuresboard/futures.db")
                print(f"get_latest_metrics returned {len(rows)} rows (type: {type(rows)})")
            except Exception:
                print("get_latest_metrics call failed:")
                traceback.print_exc()
        else:
            print("db_mod.get_latest_metrics not found")
    except Exception:
        print("Error while checking db_mod:")
        traceback.print_exc()

print("\n== Calling init_app() ==")
if app_mod:
    try:
        # attempt to create the app; pass None so init_app uses default config path behavior
        init_app = getattr(app_mod, "init_app", None)
        if callable(init_app):
            app = init_app()
            print("init_app() returned:", type(app), app)
            # if it's a flask app, print some helpful props
            try:
                import flask
                if isinstance(app, flask.Flask):
                    print("App name:", app.name)
                    print("Registered blueprints:", list(app.blueprints.keys()))
            except Exception:
                pass
        else:
            print("init_app not defined or not callable in app_mod")
    except Exception:
        print("Calling init_app() raised:")
        traceback.print_exc()
else:
    print("app_mod import failed; skipping init_app() call")

print("\n== Additional checks (metrics endpoints helpers) ==")
if metrics_mod:
    try:
        get_all_metrics = getattr(metrics_mod, "get_all_metrics", None)
        api_history = getattr(metrics_mod, "api_history", None)
        print("get_all_metrics callable:", callable(get_all_metrics))
        print("api_history callable:", callable(api_history))
    except Exception:
        traceback.print_exc()

print("\n== Done ==")

# config/path_utils.py
"""
path_utils.py — Centralized environment loader and project path utilities
---------------------------------------------------------------------------
- Load .env from project root / home / current working directory.
- Automatically executes on import.
- Prevents duplicated code across tools and workers.
"""

from pathlib import Path
from dotenv import load_dotenv

def load_env(verbose=False):
    """
    Load environment variables from the first available .env file.
    Priority:
        1) Project root (repo/.env)
        2) Home folder (~/.env)
        3) Current working directory (./.env)
    """
    loaded = False

    project_root = Path(__file__).resolve().parents[1] / ".env"
    home_env     = Path.home() / ".env"
    cwd_env      = Path.cwd() / ".env"

    for p in (project_root, home_env, cwd_env):
        if p.exists():
            load_dotenv(p, override=True)
            if verbose:
                print(f"✅ Loaded .env from: {p}")
            loaded = True
            break

    if not loaded and verbose:
        print("⚠️ No .env found. Using system environment variables.")

# Auto-execute when imported
load_env()
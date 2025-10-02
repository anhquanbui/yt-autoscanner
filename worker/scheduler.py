
# worker/scheduler.py (v2) — stop loop cleanly on quota (exit code 88)
import os, time, sys, subprocess, pathlib

INTERVAL = int(os.getenv("DISCOVER_INTERVAL_SECONDS", "30"))
EXIT_QUOTA = 88
ROOT = pathlib.Path(__file__).resolve().parent

print(f"[scheduler] interval={INTERVAL}s", flush=True)

try:
    while True:
        code = subprocess.call([sys.executable, str(ROOT / "discover_once.py")])
        if code == EXIT_QUOTA:
            print("[scheduler] Quota exhausted. Stop loop. Update YT_API_KEY and restart.", flush=True)
            sys.exit(EXIT_QUOTA)
        time.sleep(INTERVAL)
except KeyboardInterrupt:
    print("[scheduler] Received Ctrl+C, exiting.", flush=True)
    sys.exit(0)

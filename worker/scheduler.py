
# worker/scheduler.py
# Simple loop runner that executes discover_once.py every N seconds.
# Env:
#   DISCOVER_INTERVAL_SECONDS  (default: 30)
#   LOG_DIR                    (default: logs)
import os, sys, time, subprocess, datetime, pathlib

INTERVAL = int(os.getenv("DISCOVER_INTERVAL_SECONDS", "30"))
LOG_DIR = os.getenv("LOG_DIR", "logs")

BASE_DIR = pathlib.Path(__file__).resolve().parents[1]  # project root
WORKER = BASE_DIR / "worker" / "discover_once.py"
LOG_PATH = BASE_DIR / LOG_DIR
LOG_PATH.mkdir(parents=True, exist_ok=True)

def run_once():
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_file = LOG_PATH / f"discover-{datetime.datetime.now():%Y%m%d}.log"
    cmd = [sys.executable, "-u", str(WORKER)]
    print(f"[{ts}] Running: {cmd} (interval={INTERVAL}s)")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"\n\n[{ts}] === RUN START ===\n")
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        f.write(proc.stdout)
        f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] === RUN END (code={proc.returncode}) ===\n")
    return proc.returncode

def main():
    while True:
        start = time.monotonic()
        try:
            run_once()
        except KeyboardInterrupt:
            print("Stopped by user.")
            break
        except Exception as e:
            print(f"Scheduler error: {e}", file=sys.stderr)
        elapsed = time.monotonic() - start
        sleep_for = max(0.0, INTERVAL - elapsed)
        time.sleep(sleep_for)

if __name__ == "__main__":
    main()

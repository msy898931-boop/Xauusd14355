"""Keep the XAUUSD chart current: gather, rebuild, sleep, repeat.

The chart should not need a human to run a command before it can be trusted.
This loop refreshes `chart_live/` in place, and the static server in front of it
serves that directory and nothing else -- the first server served the repository
root, which put the trade journal and the agent memory on a public URL.

    CHART_OUT_DIR=/workspace/project/chart_live python3 chart_server.py

Interval via `CHART_REFRESH_S` (default 120). A failed pass is logged and the
loop tries again: one bad fetch must not end an always-on process.
"""
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent
OUT = os.environ.get("CHART_OUT_DIR") or str(ROOT / "chart_live")
EVERY = float(os.environ.get("CHART_REFRESH_S", "120"))


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}Z {msg}"
    print(line, flush=True)
    try:
        with open(pathlib.Path(OUT) / "refresh.log", "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def main() -> int:
    env = dict(os.environ, CHART_OUT_DIR=OUT)
    pathlib.Path(OUT).mkdir(parents=True, exist_ok=True)
    log(f"chart loop up -- every {EVERY:g}s into {OUT}")
    while True:
        t0 = time.time()
        try:
            g = subprocess.run([sys.executable, "-m", "desk.cli", "gather"],
                               cwd=str(ROOT / "marketdesk"), env=env,
                               capture_output=True, text=True, timeout=180)
            if g.returncode != 0:
                log(f"gather rc={g.returncode}: {(g.stderr or g.stdout)[-200:]}")
            c = subprocess.run([sys.executable, str(ROOT / "make_chart.py")],
                               env=env, capture_output=True, text=True, timeout=180)
            if c.returncode == 0:
                log("ok " + (c.stdout or "").strip().splitlines()[-1])
            else:
                log(f"chart rc={c.returncode}: {(c.stderr or '')[-200:]}")
        except subprocess.TimeoutExpired:
            log("timeout -- retrying next pass")
        except Exception as exc:            # one bad pass must not end the loop
            log(f"error: {exc!r}")
        time.sleep(max(10.0, EVERY - (time.time() - t0)))


if __name__ == "__main__":
    raise SystemExit(main())

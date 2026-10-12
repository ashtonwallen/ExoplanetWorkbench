"""One bounded science subprocess at a time. HTTP never owns scientific execution."""
import os
import subprocess
import sys
import time

from . import store


def remote_stage_timeout(stage, seconds):
    prefixes = ('Searching ', 'Querying MAST', 'Checking available light-curve',
                'Comparing TESS coverage', 'Comparing known-host', 'Cross-referencing NASA')
    return stage.startswith(prefixes) and seconds > 180


def main():
    store.init()
    store.recover()
    stop_file = store.DATA / 'worker.stop'
    stop_file.unlink(missing_ok=True)
    child = None
    active = None
    try:
        while True:
            if stop_file.exists():
                break
            active = store.claim_job()
            if not active:
                time.sleep(.75)
                continue
            env = {**os.environ, "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2", "MKL_NUM_THREADS": "2", "PYTHONUTF8": "1"}
            child = subprocess.Popen([sys.executable, "-m", "exodiscovery.execute", active["id"]], env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            start = time.monotonic()
            limit = active["params"].get("timeout_seconds", 1800)
            last_stage, stage_started = None, start
            while child.poll() is None:
                if stop_file.exists():
                    return
                job = store.get_job(active["id"])
                if job['stage'] != last_stage:
                    last_stage, stage_started = job['stage'], time.monotonic()
                stalled = remote_stage_timeout(job['stage'], time.monotonic()-stage_started)
                if job["cancel"] or time.monotonic()-start > limit or stalled:
                    child.terminate()
                    child.wait(timeout=15)
                    status = "cancelled" if job["cancel"] else "interrupted"
                    store.update_job(job["id"], status=status, stage="Stopped; saved downloads and completed artifacts retained",
                                     error=None if job["cancel"] else
                                     f"Remote stage exceeded 180 seconds: {job['stage']}. Cached inputs retained; resume explicitly." if stalled else
                                     "Time limit reached. Resume or narrow the search.")
                    break
                time.sleep(.5)
            job = store.get_job(active["id"])
            if job["status"] == "running":
                store.update_job(job["id"], status="failed", error="Worker process exited unexpectedly; cached inputs retained")
            child, active = None, None
    finally:
        if child and child.poll() is None:
            child.terminate()
            child.wait(timeout=15)
        if active:
            store.update_job(active["id"], status="interrupted", stage="Worker stopped; resume available")


if __name__ == "__main__":
    main()

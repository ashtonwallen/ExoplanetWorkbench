"""Single-command local launcher: python run.py [--no-browser] [--setup-only]."""
import argparse
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import venv
import webbrowser

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="Start EXODISCOVERY on http://127.0.0.1:8765")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--setup-only", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT)
    python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        print("Creating Python environment…", flush=True)
        venv.create(ROOT / ".venv", with_pip=True)
    check = subprocess.run([str(python), "-c", "import exodiscovery,fastapi,lightkurve,batman,setuptools"], capture_output=True)
    if check.returncode:
        subprocess.run([str(python), "-m", "pip", "install", "-e", ".[test]"], check=True)
    if args.rebuild or not (ROOT / "frontend/dist/index.html").exists():
        npm = shutil.which("npm")
        if not npm:
            raise SystemExit("Node.js 20+ is required for the first frontend build. Install Node.js, then run again.")
        subprocess.run([npm, "ci" if (ROOT / "frontend/package-lock.json").exists() else "install"], cwd=ROOT / "frontend", check=True)
        subprocess.run([npm, "run", "build"], cwd=ROOT / "frontend", check=True)
    if args.setup_only:
        print("Setup complete.")
        return
    try:
        urllib.request.urlopen("http://127.0.0.1:8765/api/health", timeout=2)
        raise SystemExit("Port 8765 already has an application running. Open http://127.0.0.1:8765 or stop it before starting another worker.")
    except OSError:
        pass
    env = {**os.environ, "PYTHONUTF8": "1", "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2", "MKL_NUM_THREADS": "2"}
    # A lifetime file lock prevents multiple launchers from creating competing workers.
    data = Path(env.get("EXO_DATA_DIR", ROOT / "data"))
    data.mkdir(parents=True, exist_ok=True)
    lock = (data / "launcher.lock").open("a+b")
    lock.seek(0)
    if os.name == "nt":
        import msvcrt
        if lock.read(1) == b"":
            lock.write(b"0"); lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit("Another EXODISCOVERY launcher is using this data directory.")
    else:
        import fcntl
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    worker = subprocess.Popen([str(python), "-m", "exodiscovery.worker"], env=env)
    server = subprocess.Popen([str(python), "-m", "uvicorn", "exodiscovery.api:app", "--host", "127.0.0.1", "--port", "8765", "--no-access-log"], env=env)
    def stop(*_):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, stop)
    try:
        for _ in range(100):
            if server.poll() is not None or worker.poll() is not None:
                raise RuntimeError("Backend or worker exited during startup")
            try:
                urllib.request.urlopen("http://127.0.0.1:8765/api/health", timeout=1)
                break
            except OSError:
                time.sleep(.3)
        print("EXODISCOVERY: http://127.0.0.1:8765  (Ctrl+C to stop)", flush=True)
        if not args.no_browser:
            webbrowser.open("http://127.0.0.1:8765")
        while server.poll() is None and worker.poll() is None:
            time.sleep(.5)
    except KeyboardInterrupt:
        print("Stopping EXODISCOVERY. Interrupted jobs can be resumed.", flush=True)
    finally:
        # On Windows children do not receive terminate signals recursively. The worker
        # observes this stop file and exits through its cleanup handler.
        (data / "worker.stop").write_text("stop")
        try:
            worker.wait(timeout=20)
        except subprocess.TimeoutExpired:
            worker.terminate()
            worker.wait(timeout=10)
        server.terminate()
        server.wait(timeout=10)
        lock.close()


if __name__ == "__main__":
    main()

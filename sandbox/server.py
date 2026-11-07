"""Python sandbox for 石英's shrink_run_python tool.

Runs in its own container (no network route out, read-only filesystem, no access to bot data);
each request executes in a fresh subprocess with CPU, memory, file and process limits."""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import resource
except ImportError:  # Windows development machines; limits only apply on Linux.
    resource = None

PORT = int(os.environ.get("SANDBOX_PORT", "8765"))
WALL_SECONDS = 10
CPU_SECONDS = 8
MEMORY_BYTES = 512 * 1024 * 1024
FILE_BYTES = 5 * 1024 * 1024
OUTPUT_LIMIT = 6000
CODE_LIMIT = 8000
SLOTS = threading.BoundedSemaphore(2)


def limit_child():
    os.setsid()
    if resource is None:
        return
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS + 1))
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_BYTES, FILE_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def clip(data):
    text = data.decode("utf-8", "replace")
    return text if len(text) <= OUTPUT_LIMIT else text[:OUTPUT_LIMIT] + "\n…（输出太长，已截断）"


def run(code):
    work = tempfile.mkdtemp(prefix="run-")
    try:
        path = os.path.join(work, "main.py")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(code)
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": work,
            "TMPDIR": work,
            "MPLCONFIGDIR": work,
            "PYTHONIOENCODING": "utf-8",
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
        }
        process = subprocess.Popen(
            [sys.executable, "-I", path],
            cwd=work,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=limit_child if os.name == "posix" else None,
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=WALL_SECONDS)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            stdout, stderr = process.communicate()
        # The CPU limit usually fires before the wall clock for busy loops.
        if os.name == "posix" and process.returncode in (-signal.SIGXCPU, -signal.SIGKILL):
            timed_out = True
        return {
            "stdout": clip(stdout),
            "stderr": clip(stderr),
            "exit": process.returncode,
            "timeout": timed_out,
            "limit": WALL_SECONDS,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, body):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self.reply(200, {"status": "ok"})
        else:
            self.reply(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/run":
            self.reply(404, {"error": "not found"})
            return
        try:
            size = int(self.headers.get("Content-Length") or 0)
            if size > CODE_LIMIT * 4:
                raise ValueError("too large")
            code = json.loads(self.rfile.read(size) or b"{}").get("code", "")
            if not isinstance(code, str) or not code.strip() or len(code) > CODE_LIMIT:
                raise ValueError("bad code")
        except (ValueError, json.JSONDecodeError):
            self.reply(400, {"error": "bad request"})
            return
        if not SLOTS.acquire(timeout=WALL_SECONDS):
            self.reply(503, {"error": "busy"})
            return
        try:
            self.reply(200, run(code))
        finally:
            SLOTS.release()

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

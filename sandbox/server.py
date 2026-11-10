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
PLUGIN_LIMIT = 8 * 1024 * 1024
PLUGIN_FILE_LIMIT = 512
SLOTS = threading.BoundedSemaphore(2)


def limit_child(cpu_seconds=CPU_SECONDS, memory_bytes=MEMORY_BYTES):
    os.setsid()
    if resource is None:
        return
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_BYTES, FILE_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def limit_plugin_child():
    limit_child(40, 1536 * 1024 * 1024)
    # The internal Docker network still reaches the bot. Block outbound IP
    # sockets in the test process itself; UNIX socketpairs remain usable by asyncio.
    import ctypes
    import ctypes.util
    library = ctypes.util.find_library("seccomp")
    if not library:
        raise RuntimeError("libseccomp is required for isolated plugin tests")
    lib = ctypes.CDLL(library, use_errno=True)
    class Compare(ctypes.Structure):
        _fields_ = [("arg", ctypes.c_uint), ("op", ctypes.c_int),
                    ("datum_a", ctypes.c_uint64), ("datum_b", ctypes.c_uint64)]
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
                                         ctypes.c_uint, ctypes.POINTER(Compare)]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    ctx = lib.seccomp_init(0x7fff0000)
    if not ctx:
        raise RuntimeError("seccomp initialization failed")
    try:
        syscall = lib.seccomp_syscall_resolve_name(b"socket")
        for family in (2, 10, 17):  # IPv4, IPv6 and packet sockets
            comparison = Compare(0, 4, family, 0)
            if lib.seccomp_rule_add_array(ctx, 0x50000 | 1, syscall, 1,
                                          ctypes.byref(comparison)) != 0:
                raise RuntimeError("seccomp rule failed")
        if lib.seccomp_load(ctx) != 0:
            raise RuntimeError("seccomp activation failed")
    finally:
        lib.seccomp_release(ctx)


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


def test_plugin(files):
    """Run authored tests against the installed AstrBot package, without bot data."""
    from pathlib import Path
    if not isinstance(files, dict) or not files:
        raise ValueError("invalid plugin files")
    if len(files) > PLUGIN_FILE_LIMIT:
        raise ValueError(f"plugin file count {len(files)} exceeds {PLUGIN_FILE_LIMIT}")
    if not any(Path(name).name.startswith("test_") and name.endswith(".py") for name in files):
        raise ValueError("插件必须提供 test_*.py 功能测试")
    with tempfile.TemporaryDirectory(prefix="plugin-test-") as directory:
        root = Path(directory)
        for name, content in files.items():
            path = Path(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ValueError("invalid path")
            if not isinstance(content, str) or len(content.encode()) > 256 * 1024:
                raise ValueError("invalid content")
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        runner = """import sys,unittest
sys.path[:0]=[sys.argv[1],'/AstrBot']
suite=unittest.defaultTestLoader.discover(sys.argv[1],pattern='test_*.py')
count=suite.countTestCases()
if not count: raise SystemExit('No tests discovered')
result=unittest.TextTestRunner(verbosity=2).run(suite)
print('SHRINK_TEST_COUNT='+str(count))
raise SystemExit(0 if result.wasSuccessful() else 1)
"""
        driver = root / "_run_tests.py"
        driver.write_text(runner, encoding="utf-8")
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            process = subprocess.Popen(
                [sys.executable, "-I", str(driver), str(root)], cwd=root,
                env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": directory,
                     "TMPDIR": directory, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
                stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                preexec_fn=limit_plugin_child if os.name == "posix" else None,
            )
            timed_out = False
            try:
                process.wait(timeout=55)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            out.seek(0)
            err.seek(0)
            stdout, stderr = clip(out.read(OUTPUT_LIMIT * 4)), clip(err.read(OUTPUT_LIMIT * 4))
        return {"exit": process.returncode, "timeout": timed_out, "stdout": stdout,
                "stderr": stderr, "validation": "isolated_unittest",
                "ok": process.returncode == 0 and "SHRINK_TEST_COUNT=" in stdout}


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
        if self.path not in ("/run", "/test-plugin"):
            self.reply(404, {"error": "not found"})
            return
        try:
            size = int(self.headers.get("Content-Length") or 0)
            if size < 0 or size > (PLUGIN_LIMIT if self.path == "/test-plugin" else CODE_LIMIT * 4):
                raise ValueError("too large")
            body = json.loads(self.rfile.read(size) or b"{}")
            code = body.get("code", "")
            if self.path == "/run" and (not isinstance(code, str) or not code.strip() or len(code) > CODE_LIMIT):
                raise ValueError("bad code")
        except (ValueError, json.JSONDecodeError):
            self.reply(400, {"error": "bad request"})
            return
        if not SLOTS.acquire(timeout=WALL_SECONDS):
            self.reply(503, {"error": "busy"})
            return
        try:
            self.reply(200, test_plugin(body.get("files")) if self.path == "/test-plugin" else run(code))
        except ValueError as exc:
            self.reply(400, {"error": str(exc)})
        finally:
            SLOTS.release()

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

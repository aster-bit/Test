"""Trusted runner executed *inside* the sandbox (subprocess or container).

Protocol: one JSON document on stdin
    {"segments": [{"name", "source", "params"}], "states": [...], "item_timeout_s": float}
and one JSON document on the real stdout:
    {"results": [{"state", "error", "ms"}], "setup_error": str|null}

Defense in depth (the OS-level isolation is applied by the parent):
* only allow-listed stdlib modules are importable;
* a PEP 578 audit hook blocks file opens, sockets, subprocesses, forks,
  ctypes, dynamic code compilation and new imports after setup;
* candidate stdout/stderr is captured so it cannot corrupt the protocol;
* each item runs under its own SIGALRM timer.

This file must stay standard-library only: it is copied into containers.
"""

import io
import json
import signal
import sys
import time

ALLOWED_MODULES = (
    "re", "json", "math", "datetime", "decimal", "unicodedata", "itertools",
    "collections", "string", "functools", "statistics", "fractions", "operator", "_strptime",
    "heapq", "bisect", "random", "copy", "time", "typing", "dataclasses", "enum",
)


class ItemTimeout(Exception):
    pass


def _alarm(signum, frame):
    raise ItemTimeout("per-item timeout exceeded")


def main() -> None:
    real_stdout = sys.stdout
    payload = json.loads(sys.stdin.read())
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()

    for m in ALLOWED_MODULES:
        __import__(m)

    steps = []
    setup_error = None
    try:
        for seg in payload["segments"]:
            ns = {"__name__": "candidate_" + seg["name"], "__builtins__": __builtins__}
            exec(compile(seg["source"], "<" + seg["name"] + ">", "exec"), ns)
            if "step" not in ns:
                raise ValueError("segment %s defines no step()" % seg["name"])
            steps.append((seg["name"], ns["step"], seg.get("params", {})))
    except Exception as e:  # noqa: BLE001 - reported to host
        setup_error = "%s: %s" % (type(e).__name__, e)

    blocked_prefixes = ("open", "socket.", "subprocess.", "os.system", "os.exec", "os.spawn", "os.fork",
                        "os.posix_spawn", "ctypes.", "shutil.", "os.remove", "os.rename", "os.mkdir",
                        "os.rmdir", "os.chmod", "os.kill", "exec", "compile", "import", "urllib.",
                        "http.", "ftplib.", "smtplib.", "builtins.input", "sys.addaudithook")
    allowed_imports = set(sys.modules)

    def hook(event, args):
        if event == "import":
            if args and args[0] in allowed_imports:
                return
            raise PermissionError("sandbox: import blocked: %s" % (args[0] if args else "?"))
        if event.startswith(blocked_prefixes):
            raise PermissionError("sandbox: blocked operation: %s" % event)

    sys.addaudithook(hook)
    signal.signal(signal.SIGALRM, _alarm)
    timeout = float(payload.get("item_timeout_s", 2.0))

    results = []
    for state in payload["states"]:
        if setup_error is not None:
            results.append({"state": state, "error": "setup: " + setup_error, "ms": 0.0})
            continue
        t0 = time.perf_counter()
        err = None
        try:
            signal.setitimer(signal.ITIMER_REAL, timeout)
            for name, fn, params in steps:
                state = fn(state, params)
                if not isinstance(state, dict):
                    raise TypeError("step %s returned %s, expected dict" % (name, type(state).__name__))
        except BaseException as e:  # noqa: BLE001 - includes RecursionError, MemoryError
            err = "%s: %s" % (type(e).__name__, str(e)[:300])
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
        ms = (time.perf_counter() - t0) * 1000.0
        try:
            json.dumps(state)
        except (TypeError, ValueError) as e:
            err = err or "unserializable state: %s" % e
            state = {}
        results.append({"state": state, "error": err, "ms": ms})

    real_stdout.write(json.dumps({"results": results, "setup_error": setup_error}))
    real_stdout.flush()


if __name__ == "__main__":
    main()

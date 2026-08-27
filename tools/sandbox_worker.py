"""Persistent sandbox worker subprocess.

Long-lived child process that executes one pandas snippet per request.  It is
launched once by tools/run_code.py and reused across all charts, so the pandas
import and interpreter startup cost is paid a single time.  It never imports
Streamlit and has no access to the host application.

Protocol (stdio, single-line frames, base64-encoded):
  in:  {"df": <b64 pickle of DataFrame>, "code": <b64 utf-8 code>}
  out: {"ok": true, "payload": {...}}          on success
       {"ok": false, "blocked": true, "error": ...}   static-analysis rejection
       {"ok": false, "blocked": false, "error": ...}  runtime error
  The parent kills this process (and starts a fresh one) when a snippet times out.
"""

from __future__ import annotations

import base64
import json
import os
import pickle
import sys

# Running as a script puts tools/ on sys.path; make the project root importable
# so `import tools.run_code` resolves regardless of how we were launched.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.run_code import (
    SandboxBlockedError,
    SandboxExecutionError,
    _exec_user_code,
)


def _send(payload: dict) -> None:
    raw = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    sys.stdout.write(raw + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            df = pickle.loads(base64.b64decode(req["df"]))
            code = base64.b64decode(req["code"]).decode("utf-8")
            result = _exec_user_code(df, code)
            _send({"ok": True, "payload": result})
        except SandboxBlockedError as exc:
            _send({"ok": False, "blocked": True, "error": str(exc)})
        except SandboxExecutionError as exc:
            _send({"ok": False, "blocked": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - surface as data, never crash the worker
            _send({"ok": False, "blocked": False, "error": f"Sandbox internal error: {exc}"})


if __name__ == "__main__":
    main()
"""Sandboxed pandas execution tool.

`run_snippet(df, code)` executes a pandas snippet against a DataFrame in a
*dedicated, persistent subprocess* (sandbox_worker.py) -- so a runaway snippet
can be killed and the worker re-spawned -- with a hard timeout, restricted exec()
globals, and an AST-level rejection of anything that touches imports,
dunder/attribute escapes, files, or the network.

The worker is started once and reused across all charts, so the process + pandas
startup cost is paid only once (the original per-chart multiprocessing spawn was
pathologically slow under Streamlit on Windows).

The snippet must end by assigning its output to a variable named ``result``,
which may be a DataFrame, Series, or scalar.
"""

from __future__ import annotations

import ast
import base64
import json
import os
import pickle
import queue
import subprocess
import sys
import threading
from dataclasses import dataclass, field

import pandas as pd

MAX_RECORDS = 10000  # cap on rows sent back to the caller for chart building


class SandboxBlockedError(RuntimeError):
    """Raised when the snippet was rejected by static analysis."""


class SandboxExecutionError(RuntimeError):
    """Raised when the snippet failed at runtime inside the sandbox."""


# Reachable-only-from-pandas methods that touch disk/network/OS.
DANGEROUS_ATTRS = {
    "read_csv", "read_table", "read_excel", "read_json", "read_parquet",
    "read_pickle", "read_sql", "read_sql_query", "read_sql_table", "read_html",
    "read_feather", "read_stata", "read_sas", "read_hdf", "read_fwf",
    "read_clipboard", "read_gbq", "read_orc",
    "to_csv", "to_excel", "to_pickle", "to_parquet", "to_hdf", "to_sql",
    "to_clipboard", "to_gbq", "to_feather", "to_stata",
    "eval", "query",  # pandas eval/query bypass the AST restrictions
    "mro", "subclasses",
    "__reduce__", "__getstate__", "__setstate__",
}

DANGEROUS_NAMES = {
    "eval", "exec", "compile", "open", "input", "breakpoint",
    "globals", "locals", "vars", "dir", "getattr", "setattr", "delattr",
    "__import__", "importlib", "pickle", "marshal", "socket", "subprocess",
    "os", "sys", "builtins", "requests", "urllib",
}

CONSTANT_BANNED = {
    "eval", "exec", "compile", "open", "input", "breakpoint", "getattr",
    "setattr", "delattr", "vars", "globals", "locals", "__import__", "exit",
}

SAFE_BUILTINS: dict[str, object] = {
    "len": len,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "set": set,
    "tuple": tuple,
    "frozenset": frozenset,
    "range": range,
    "min": min,
    "max": max,
    "sum": sum,
    "abs": abs,
    "round": round,
    "sorted": sorted,
    "reversed": reversed,
    "enumerate": enumerate,
    "zip": zip,
    "any": any,
    "all": all,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "repr": repr,
    "type": type,
    "map": map,
    "filter": filter,
    "print": print,
    "chr": chr,
    "ord": ord,
    "divmod": divmod,
    "pow": pow,
    "oct": oct,
    "hex": hex,
    "bin": bin,
}


def validate_snippet(code: str) -> str | None:
    """Static analysis pass. Returns an error message, or None if the code is allowed.

    Rejects: import statements, calls to eval/exec/open/..., attribute access on
    dunder names, attribute access to pandas/net/file methods, and a set of
    block-listed names.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"Code does not parse: {exc}"

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return "Imports are not allowed in the sandbox."
        if isinstance(node, ast.Attribute):
            attr = node.attr
            if attr.startswith("__"):
                return f"Access to dunder attribute '{attr}' is not allowed."
            if attr in DANGEROUS_ATTRS:
                return f"Attribute '{attr}' is blocked (potential file/network/escape access)."
        if isinstance(node, ast.Name):
            if node.id in DANGEROUS_NAMES:
                return f"Name '{node.id}' is not allowed in the sandbox."
        if isinstance(node, ast.Call):
            fname = None
            if isinstance(node.func, ast.Name):
                fname = node.func.id
            elif isinstance(node.func, ast.Attribute):
                fname = node.func.attr
            if fname and fname in CONSTANT_BANNED:
                return f"Calling '{fname}' is not allowed in the sandbox."
        if isinstance(node, ast.Lambda):
            # lamdba can smuggle dangerous builtins; validate body names too (walker covers it)
            pass
    return None


def _sanitise_frame(df: pd.DataFrame) -> dict:
    df = df.reset_index(drop=True)
    trunc = len(df) > MAX_RECORDS
    if trunc:
        df = df.head(MAX_RECORDS)
    # Convert datetime columns to string for JSON serialization
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]) or df[col].dtype == object and df[col].apply(lambda x: isinstance(x, pd.Timestamp)).any():
            df[col] = df[col].astype(str)
    try:
        records = df.astype(object).where(pd.notna(df), None).to_dict(orient="records")
        # Ensure records are JSON-serializable
        for rec in records:
            for k, v in list(rec.items()):
                if isinstance(v, (pd.Timestamp,)):
                    rec[k] = str(v)
                elif hasattr(v, "isoformat"):
                    try:
                        rec[k] = v.isoformat()
                    except Exception:
                        rec[k] = str(v)
    except Exception:
        records = [dict(zip(map(str, df.columns), [str(v) if isinstance(v, pd.Timestamp) else v for v in row])) for row in df.itertuples(index=False)]
    payload = {
        "kind": "dataframe",
        "columns": [str(c) for c in df.columns],
        "shape": [len(df), df.shape[1] if len(df.shape) == 2 else 1],
        "records": records,
        "truncated": trunc,
        "preview": records[:6],
    }
    return payload


def _sanitise_scalar(value) -> dict:
    if value is None:
        return {"kind": "none"}
    if isinstance(value, pd.Series):
        try:
            payload = _sanitise_frame(value.reset_index())
            payload["kind"] = "dataframe"
            return payload
        except Exception:
            return {"kind": "list", "value": [str(v) for v in value.head(50)]}
    if isinstance(value, pd.DataFrame):
        return _sanitise_frame(value)
    try:
        return {"kind": "scalar", "value": str(value)}
    except Exception:
        return {"kind": "scalar", "value": "<?>"}


def _exec_user_code(df: pd.DataFrame, code: str) -> dict:
    """Run the snippet with restricted globals (executed *in the sandbox process*)."""
    err = validate_snippet(code)
    if err:
        raise SandboxBlockedError(err)

    namespace: dict = {
        "df": df,
        "pd": pd,
        "__builtins__": SAFE_BUILTINS,
    }
    try:
        compiled = compile(code, "<sandbox>", "exec")
        exec(compiled, namespace, namespace)
    except Exception as exc:  # noqa: BLE001 - we surface user errors as data
        raise SandboxExecutionError(f"{type(exc).__name__}: {exc}") from exc

    if "result" not in namespace:
        raise SandboxExecutionError(
            "The snippet must assign its output to a variable named 'result'."
        )
    return _sanitise_scalar(namespace["result"])


def run_snippet(df: pd.DataFrame, code: str, timeout: float | None = None) -> RunResult:
    """Execute `code` against `df` in the persistent sandbox subprocess.

    One dedicated worker is started lazily and reused for every chart, so the
    process + pandas startup cost is paid only once.  A hard timeout is enforced
    by the parent: on timeout the worker is killed and a fresh one respawned.
    Requests are serialised under a lock so concurrent callers never interleave
    frames on the worker's pipe.

    Returns a RunResult; never raises for a bad snippet.
    """
    if timeout is None:
        timeout = float(os.environ.get("CHARTCOPILOT_TIMEOUT", "8") or 8)

    err = validate_snippet(code)
    if err:
        return RunResult(ok=False, blocked=True, error=err)

    frame = json.dumps(
        {"df": base64.b64encode(pickle.dumps(df)).decode("ascii"),
         "code": base64.b64encode(code.encode("utf-8")).decode("ascii")},
        separators=(",", ":"),
    ) + "\n"

    with _WORKER_LOCK:
        for _ in range(2):  # one immediate retry with a fresh worker if it died
            proc = _get_worker_nolock()
            if proc is None:
                return RunResult(ok=False, error="Could not start the sandbox worker process.")

            out_q: queue.Queue = queue.Queue(maxsize=1)
            reader = threading.Thread(
                target=_read_reply, args=(proc, out_q), daemon=True, name="sandbox-reader"
            )
            reader.start()
            try:
                proc.stdin.write(frame.encode("utf-8"))
                proc.stdin.flush()
            except (BrokenPipeError, OSError):
                _kill_worker_nolock()
                continue

            reader.join(timeout)
            if reader.is_alive():
                # The snippet is still running: enforce the kill switch.
                _kill_worker_nolock()
                return RunResult(ok=False, timed_out=True,
                                 error="Timed out (sandbox timeout exceeded).")

            try:
                payload = out_q.get_nowait()
            except queue.Empty:
                payload = None
            if payload is None:
                _kill_worker_nolock()
                continue  # worker died mid-request; retry once with a fresh worker

            return _from_payload(payload)

    return RunResult(ok=False, error="Sandbox worker failed repeatedly; please retry.")


# --- persistent worker management ---------------------------------------------

_WORKER = None
_WORKER_LOCK = threading.Lock()


def _worker_script() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "sandbox_worker.py")


def _start_worker_nolock():
    global _WORKER
    try:
        _WORKER = subprocess.Popen(
            [sys.executable, "-u", _worker_script()],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001
        _WORKER = None
        raise RuntimeError(f"Failed to start sandbox worker: {exc}") from exc


def _get_worker():
    with _WORKER_LOCK:
        return _get_worker_nolock()


def _get_worker_nolock():
    global _WORKER
    if _WORKER is None or _WORKER.poll() is not None:
        _start_worker_nolock()
    return _WORKER


def _kill_worker():
    with _WORKER_LOCK:
        _kill_worker_nolock()


def _kill_worker_nolock() -> None:
    global _WORKER
    proc = _WORKER
    _WORKER = None
    if proc is None:
        return
    try:
        proc.stdin.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass


def _read_reply(proc, out_q: queue.Queue) -> None:
    try:
        line = proc.stdout.readline()
        if not line:
            out_q.put(None)
            return
        payload = json.loads(base64.b64decode(line.strip()).decode("utf-8"))
        out_q.put(payload)
    except Exception:  # noqa: BLE001
        out_q.put(None)


@dataclass
class RunResult:
    ok: bool
    kind: str = "none"
    preview: list = field(default_factory=list)
    records: list = field(default_factory=list)
    columns: list = field(default_factory=list)
    shape: tuple = (0, 0)
    scalar: str | None = None
    error: str | None = None
    blocked: bool = False
    timed_out: bool = False

    def to_text(self, max_rows: int = 6) -> str:
        if not self.ok:
            tag = "BLOCKED" if self.blocked else ("TIMEOUT" if self.timed_out else "ERROR")
            return f"[{tag}] {self.error}"
        if self.kind == "dataframe":
            return (
                f"Result: DataFrame with {self.shape[0]} rows x {self.shape[1]} columns "
                f"({', '.join(self.columns)})\nPreview (first {len(self.preview[:max_rows])} rows):\n"
                + "\n".join(str(r) for r in self.preview[:max_rows])
            )
        if self.kind in ("scalar", "list"):
            return f"Result: {self.scalar or ''} ({self.kind})"
        return "Result: None"


def _from_payload(payload: dict, timed_out: bool = False) -> RunResult:
    if timed_out:
        return RunResult(ok=False, timed_out=True, error="Timed out (sandbox timeout exceeded).")
    if not payload.get("ok"):
        return RunResult(
            ok=False,
            blocked=bool(payload.get("blocked")),
            error=payload.get("error", "Sandbox execution failed."),
        )
    body = payload.get("payload", payload)
    kind = body.get("kind", "none")
    if kind == "dataframe":
        return RunResult(
            ok=True,
            kind="dataframe",
            preview=body.get("preview", []),
            records=body.get("records", []),
            columns=body.get("columns", []),
            shape=tuple(body.get("shape", [0, 0])),
        )
    if kind in ("scalar", "list", "none"):
        return RunResult(ok=True, kind=kind, scalar=str(body.get("value", "")))
    return RunResult(ok=True, kind=kind, scalar=repr(body)[:200])


def reconstruct_df(result: RunResult) -> pd.DataFrame | None:
    """Rebuild a DataFrame from a RunResult's records for chart building."""
    if not result.ok or result.kind != "dataframe" or not result.records:
        return None
    return pd.DataFrame(result.records)
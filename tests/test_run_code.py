"""Isolation tests for the sandboxed pandas execution tool.

Run from the chartcopilot/ directory:
    pytest tests/test_run_code.py -v
"""

from __future__ import annotations

import unittest

import pandas as pd

from tools.run_code import run_snippet, reconstruct_df


def make_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Region": ["North", "South", "North", "South", "East", "East"],
            "Units_Sold": [10, 20, 15, 25, 30, 5],
        }
    )


class TestSandboxValidSnippets(unittest.TestCase):
    def test_groupby_aggregation_returns_frame(self):
        df = make_df()
        code = (
            "result = df.groupby('Region')['Units_Sold']"
            ".sum().reset_index().sort_values('Units_Sold', ascending=False)"
        )
        res = run_snippet(df, code)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.kind, "dataframe")
        out = reconstruct_df(res)
        self.assertIsNotNone(out)
        self.assertEqual(out.shape[0], 3)
        self.assertEqual(out.loc[out.Region == "East", "Units_Sold"].iloc[0], 35)

    def test_scalar_result(self):
        res = run_snippet(make_df(), "result = df['Units_Sold'].sum()")
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.kind, "scalar")
        self.assertEqual(res.scalar, "105")

    def test_count_result(self):
        res = run_snippet(make_df(), "result = df['Region'].value_counts().rename_axis('Region').reset_index(name='count')")
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.kind, "dataframe")


class TestSandboxBlocksMaliciousCode(unittest.TestCase):
    def test_import_statement_blocked(self):
        for code in ("import os", "import sys", "from os import system"):
            res = run_snippet(make_df(), code + "\nresult = 1")
            self.assertFalse(res.ok, code)
            self.assertTrue(res.blocked, code)

    def test_os_system_blocked(self):
        res = run_snippet(make_df(), "result = os.system('dir')")
        self.assertFalse(res.ok)
        self.assertTrue(res.blocked)

    def test_dunder_attribute_blocked(self):
        for code in (
            "result = ().__class__.__bases__[0].__subclasses__()",
            "result = df.__class__",
            "result = ('x'.__class__)",
        ):
            res = run_snippet(make_df(), code)
            self.assertFalse(res.ok, code)
            self.assertTrue(res.blocked, code)
            self.assertIn("dunder", res.error.lower(), code)

    def test_eval_exec_open_blocked(self):
        for code in ("result = eval('1+1')", "result = exec('1')", "result = open('x')"):
            res = run_snippet(make_df(), code)
            self.assertFalse(res.ok, code)
            self.assertTrue(res.blocked, code)

    def test_pandas_file_io_blocked(self):
        for code in ("result = pd.read_csv('/etc/passwd')", "result = df.to_csv('out.csv')"):
            res = run_snippet(make_df(), code)
            self.assertFalse(res.ok, code)
            self.assertTrue(res.blocked, code)

    def test_missing_result_variable_rejected(self):
        res = run_snippet(make_df(), "x = 1 + 1")
        self.assertFalse(res.ok)
        self.assertIn("result", res.error)

    def test_infinite_loop_times_out(self):
        res = run_snippet(make_df(), "while True:\n    pass", timeout=3.0)
        self.assertFalse(res.ok)
        self.assertTrue(res.timed_out)

    def test_worker_recovers_after_timeout(self):
        """After a killed runaway snippet, the worker is respawned and still works."""
        bad = run_snippet(make_df(), "while True:\n    pass", timeout=2.0)
        self.assertTrue(bad.timed_out)
        good = run_snippet(make_df(), "result = df['Units_Sold'].sum()", timeout=8.0)
        self.assertTrue(good.ok, good.error)
        self.assertEqual(good.scalar, "105")

    def test_worker_reused_across_calls(self):
        """Multiple snippets execute against the same long-lived worker."""
        df = make_df()
        for code in (
            "result = df['Units_Sold'].sum()",
            "result = df.groupby('Region')['Units_Sold'].sum().reset_index()",
            "result = df['Units_Sold'].max()",
        ):
            res = run_snippet(df, code, timeout=8.0)
            self.assertTrue(res.ok, res.error)


if __name__ == "__main__":
    unittest.main()
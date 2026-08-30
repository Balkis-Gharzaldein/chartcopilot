"""Gate protocol."""

from __future__ import annotations

from dataclasses import dataclass

@dataclass
class GateResult:
    passed: bool
    reason: str
    gate: str  # CAN | APPROPRIATE | USEFUL

def gate_pass(reason: str = "passed") -> GateResult:
    return GateResult(passed=True, reason=reason, gate="")

def gate_fail(reason: str, gate: str) -> GateResult:
    return GateResult(passed=False, reason=reason, gate=gate)

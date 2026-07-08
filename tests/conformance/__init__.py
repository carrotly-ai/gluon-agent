"""Conformance harness for the loop engine (Phase D of the hardening plan).

These tests drive the REAL LoopManager / claim_work / work-queue / merge-back
primitives against fake, deterministic worker outcomes — no Claude calls — and
include genuine cross-process scenarios (multiprocessing + real git). Each test
maps to a session-audit finding or a load-bearing invariant, and each was
written to be RED against the pre-fix code and GREEN after. See
docs/design/loop-hardening-and-discipline-plan.md (Phase D).
"""

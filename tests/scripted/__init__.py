"""Scripted-harness testing: drive run_game.run_game() with deterministic
agents instead of real LLMs.

See ``[[feedback-live-harness-tests]]``: this is the preferred testing shape
for harness-level changes — real run_game loop + deterministic engine state
(via ``librarySeed``) + scripted agent responses. Asserts are on the
resulting game.jsonl events.

Also doubles as a DSPy-ready corpus: each ScriptedAgent call records the
prompt that WOULD have been sent to an LLM alongside the scripted response,
so the test set can later be reused as labeled examples for prompt
optimization (see deep-honking-gadget Phase 0.4).
"""

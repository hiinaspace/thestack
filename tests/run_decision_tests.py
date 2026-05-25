"""CLI: drive structured-decision fixtures through a chosen LLM backend.

  uv run python -m tests.run_decision_tests --kind distribute
  uv run python -m tests.run_decision_tests --kind select_cards --model gemma4:26b
  uv run python -m tests.run_decision_tests --fixture tests/fixtures/distribute/forked_lightning_three_targets.json

Reflection-channel markdown lands in tests/fixtures/<kind>/reflections/.
Use --no-reflect to skip the meta-reflection turn (faster smoke runs).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from llm.client import DEFAULT_MODEL
from tests.decision_runner import (
    FIXTURE_ROOT,
    list_fixtures,
    run_fixture,
    summarize,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kind",
        default=None,
        help="Limit to one decision kind subdir (e.g. distribute, select_cards, search_library).",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=None,
        help="Run a single fixture path; overrides --kind.",
    )
    parser.add_argument(
        "--backend",
        default="ollama",
        choices=("ollama", "anthropic_sdk"),
        help="Which LLM backend to drive. 'anthropic_sdk' uses the Claude Agent "
        "SDK against your subscription credentials (claude /login).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            f"Model name. Ollama default: {DEFAULT_MODEL}. "
            "Claude SDK: pass 'haiku', 'sonnet', 'opus', or a dated id "
            "(e.g. claude-haiku-4-5-20251001)."
        ),
    )
    parser.add_argument(
        "--no-reflect",
        action="store_true",
        help="Skip the meta-reflection turn (faster; no reflection markdown written).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Echo per-tool-call output and thinking excerpts to stdout.",
    )
    args = parser.parse_args()

    fixtures = [args.fixture.resolve()] if args.fixture else list_fixtures(args.kind)

    if not fixtures:
        scope = f"kind={args.kind!r}" if args.kind else "any kind"
        raise SystemExit(f"No fixtures found under {FIXTURE_ROOT} for {scope}.")

    model = args.model or (DEFAULT_MODEL if args.backend == "ollama" else "haiku")
    print(f"Running {len(fixtures)} fixture(s) via {args.backend} / {model}\n")
    results = []
    for fp in fixtures:
        try:
            label = fp.relative_to(FIXTURE_ROOT.parent)
        except ValueError:
            label = fp
        print(f"--- {label} ---")
        result = run_fixture(
            fp,
            model=model,
            backend=args.backend,
            verbose=args.verbose,
            skip_reflection=args.no_reflect,
        )
        results.append(result)
        print(f"  decisionId: {result.decision_id}")
        print(f"  valid: {result.valid}  (fallback used: {result.used_fallback})")
        for note in result.validation_notes:
            print(f"    ! {note}")
        if result.reflection_path:
            print(f"  reflection: {result.reflection_path}")
        print()

    print("=" * 60)
    print(summarize(results))


if __name__ == "__main__":
    main()

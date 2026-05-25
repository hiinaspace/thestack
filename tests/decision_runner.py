"""Pump a single structured-decision fixture through PlayerAgent.choose_decision.

Used both as a local diagnostic (does e4b produce a legal DecisionResponse for
DISTRIBUTE / SELECT_CARDS / SEARCH_LIBRARY?) and as a cheap test bed for
Phase B Claude Agent SDK runs (no Argentum required — fixtures replay
deterministically).

The runner intentionally bypasses Argentum: the harness inputs a captured
observation, the player agent produces a response, and we validate the
response shape locally. Reflection-channel text written to
``tests/fixtures/<kind>/reflections/<model>.md`` is the deliverable the user
cares about — "if you were a smaller LLM, what rough edges would you find?".

Run via ``tests/run_decision_tests.py`` (CLI) or invoke ``run_fixture`` from
another module / a notebook.
"""

from __future__ import annotations

import datetime as _dt
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import ollama

from cards.decks import default_deck_for, get_deck
from llm import oracle
from llm.client import DEFAULT_MODEL, make_client
from llm.persona import Persona
from llm.player import (
    PlayerAgent,
    _default_decision_response,
    _parse_decision_response_fallback,
    _valid_decision_response,
)
from llm.prompts import (
    build_player_system_prompt,
    format_observation,
    format_recent_public_actions,
    format_structured_decision,
)
from llm.tools import Toolbox

FIXTURE_ROOT = Path(__file__).parent / "fixtures"
REFLECTION_PROMPT = (
    "Step out of character for one moment. You just produced a structured "
    "DecisionResponse for the situation above. Reply with three short "
    "labelled sections:\n\n"
    "RATING: a number 1-5 for how clearly the harness presented this "
    "decision (5 = trivial, 1 = nearly impossible).\n\n"
    "ROUGH EDGES: 3-6 bullets on what was confusing about the prompt "
    "structure, the JSON shape, the entity-id soup, or anything else. "
    "Concrete is better than abstract.\n\n"
    "SMALLER LLM CHECK: imagine you were a 4B-parameter local model running "
    "this same prompt — list the specific places that model would most "
    "likely fail (e.g. mis-formatted JSON, hallucinated entity ids, "
    "miscounted distribution totals, picked an illegal card). Two or three "
    "bullets is enough."
)


@dataclass
class FixtureResult:
    fixture_path: Path
    kind: str
    decision_id: str
    response: dict | None
    valid: bool
    validation_notes: list[str] = field(default_factory=list)
    reasoning: str = ""
    reflection_text: str = ""
    reflection_path: Path | None = None
    # True when PlayerAgent silently replaced the model's emission with
    # llm.player._default_decision_response. The "response" field above is
    # then the fallback, not the model's output. Counts as a test failure
    # since the integration test is meant to exercise the model.
    used_fallback: bool = False
    # The raw decision_response emitted by the model via submit_decision (or
    # by the JSON-in-content fallback parser), pre-fallback. May be None if
    # the model never called submit_decision at all.
    raw_model_response: dict | None = None


def load_fixture(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def list_fixtures(kind: str | None = None) -> list[Path]:
    if kind:
        return sorted((FIXTURE_ROOT / kind).glob("*.json"))
    return sorted(p for p in FIXTURE_ROOT.glob("*/*.json") if p.parent.name != "reflections")


def run_fixture(
    fixture_path: Path,
    *,
    model: str = DEFAULT_MODEL,
    backend: str = "ollama",
    client: ollama.Client | None = None,
    verbose: bool = False,
    skip_reflection: bool = False,
) -> FixtureResult:
    """Drive one fixture through the chosen LLM backend; validate + reflect.

    backend="ollama" uses the existing PlayerAgent + native Ollama tool-call
    loop. backend="anthropic_sdk" routes through llm.claude_agent_sdk_backend
    (Phase B spike) which talks to Claude via subscription credentials.
    """
    obs = load_fixture(fixture_path)
    meta = obs.get("_meta") or {}
    perspective_name = meta.get("perspective_player_name")
    if not perspective_name:
        raise ValueError(f"Fixture {fixture_path} missing _meta.perspective_player_name")

    pd = obs.get("pendingDecision") or {}
    if not pd.get("requiresStructuredResponse"):
        raise ValueError(f"Fixture {fixture_path} pendingDecision is not structured.")

    persona = Persona(perspective_name)
    opponent_name = _opponent_name(obs, perspective_name)
    deck_name = (
        meta.get("deck")
        or default_deck_for(perspective_name)
        or _deck_for_perspective(perspective_name)
    )
    deck = get_deck(deck_name)

    if backend == "ollama":
        return _run_ollama(
            fixture_path=fixture_path,
            obs=obs,
            pd=pd,
            persona=persona,
            opponent_name=opponent_name,
            deck=deck,
            model=model,
            client=client,
            verbose=verbose,
            skip_reflection=skip_reflection,
        )
    if backend == "anthropic_sdk":
        return _run_anthropic_sdk(
            fixture_path=fixture_path,
            obs=obs,
            pd=pd,
            persona=persona,
            opponent_name=opponent_name,
            deck=deck,
            model=model,
            verbose=verbose,
            skip_reflection=skip_reflection,
        )
    raise ValueError(f"Unknown backend {backend!r}; expected 'ollama' or 'anthropic_sdk'.")


def _run_ollama(
    *,
    fixture_path: Path,
    obs: dict,
    pd: dict,
    persona: Persona,
    opponent_name: str,
    deck: dict[str, int],
    model: str,
    client: ollama.Client | None,
    verbose: bool,
    skip_reflection: bool,
) -> FixtureResult:
    client = client or make_client()
    event_log = _NullEventLog(fixture_path)
    agent = PlayerAgent(
        persona=persona,
        opponent_name=opponent_name,
        model=model,
        client=client,
        event_log=event_log,
        deck=deck,
    )

    fallback_response = agent.choose_decision(obs, verbose=verbose)
    raw_model_response = agent.toolbox.chosen_decision_response
    used_fallback = raw_model_response != fallback_response

    target_for_validation = (
        raw_model_response if raw_model_response is not None else fallback_response
    )
    valid, notes = validate_response(pd, target_for_validation)
    if used_fallback:
        notes.insert(
            0,
            "PlayerAgent.choose_decision substituted _default_decision_response "
            "for the model's emission — see raw_model_response below.",
        )
        valid = False

    result = FixtureResult(
        fixture_path=fixture_path,
        kind=pd.get("kind", "?"),
        decision_id=pd.get("decisionId", ""),
        response=target_for_validation,
        valid=valid,
        validation_notes=notes,
        reasoning=agent.last_reasoning,
        used_fallback=used_fallback,
        raw_model_response=raw_model_response,
    )

    if not skip_reflection:
        reflection = _capture_ollama_reflection(agent, verbose=verbose)
        result.reflection_text = reflection
        result.reflection_path = _write_reflection(
            fixture_path=fixture_path,
            model=model,
            response=target_for_validation,
            raw_model_response=raw_model_response,
            used_fallback=used_fallback,
            valid=valid,
            notes=notes,
            reasoning=agent.last_reasoning,
            reflection=reflection,
        )
    return result


def _run_anthropic_sdk(
    *,
    fixture_path: Path,
    obs: dict,
    pd: dict,
    persona: Persona,
    opponent_name: str,
    deck: dict[str, int],
    model: str,
    verbose: bool,
    skip_reflection: bool,
) -> FixtureResult:
    from llm.claude_agent_sdk_backend import ClaudeDecisionSession

    system_prompt = build_player_system_prompt(
        persona.name,
        opponent_name,
        identity=persona.identity,
        decklist=oracle.deck_listing(deck),
        strategy=persona.strategy,
        opponent_notes=persona.opponent_entry(opponent_name),
        recent_memory=persona.recent_memory(),
    )

    toolbox = Toolbox(name=persona.name)
    toolbox.reset_turn(set(), valid_decision_id=pd.get("decisionId"))

    user_msg = _decision_user_message(obs, persona.name)

    raw_model_response: dict | None = None
    last_reasoning = ""
    reflection_text = ""

    with ClaudeDecisionSession(
        system_prompt=system_prompt,
        toolbox=toolbox,
        model=model,
        verbose=verbose,
    ) as session:
        decision_turn = session.run_user_turn(user_msg)
        raw_model_response = decision_turn.decision_response
        last_reasoning = decision_turn.reasoning

        if not skip_reflection:
            # New tool-call turn would re-trigger submit_decision; reset the
            # toolbox state first so the reflection turn doesn't bleed into
            # the decision validation.
            toolbox.chosen_decision_response = None
            toolbox.chosen_reasoning = ""
            reflection_turn = session.run_user_turn(REFLECTION_PROMPT)
            reflection_text = reflection_turn.content.strip()

    # Mirror Ollama fallback logic so the same validation surfaces apply.
    candidate = raw_model_response
    if candidate is None:
        candidate = _parse_decision_response_fallback(decision_turn.content, pd.get("decisionId"))
    used_fallback = not _valid_decision_response(candidate, pd.get("decisionId"))
    fallback = _default_decision_response(obs) if used_fallback else candidate

    target_for_validation = raw_model_response if raw_model_response is not None else fallback
    valid, notes = validate_response(pd, target_for_validation)
    if used_fallback:
        notes.insert(
            0,
            "Model never emitted a valid structured decision; "
            "_default_decision_response would have been used in a real game.",
        )
        valid = False

    result = FixtureResult(
        fixture_path=fixture_path,
        kind=pd.get("kind", "?"),
        decision_id=pd.get("decisionId", ""),
        response=target_for_validation,
        valid=valid,
        validation_notes=notes,
        reasoning=last_reasoning,
        used_fallback=used_fallback,
        raw_model_response=raw_model_response,
    )
    if not skip_reflection:
        result.reflection_text = reflection_text
        result.reflection_path = _write_reflection(
            fixture_path=fixture_path,
            model=model,
            response=target_for_validation,
            raw_model_response=raw_model_response,
            used_fallback=used_fallback,
            valid=valid,
            notes=notes,
            reasoning=last_reasoning,
            reflection=reflection_text,
        )
    return result


def _decision_user_message(obs: dict, perspective_name: str) -> str:
    """Mirror PlayerAgent.choose_decision's user-turn construction."""
    return (
        f"{format_observation(obs, perspective_name)}\n\n"
        f"{format_recent_public_actions([])}\n\n"
        f"{format_structured_decision(obs, perspective_name)}\n\n"
        "Stay in voice. A quick monologue() line is welcome if this "
        "decision matters; otherwise just construct the DecisionResponse "
        "JSON and call submit_decision."
    )


def validate_response(pd: dict, response: dict | None) -> tuple[bool, list[str]]:
    notes: list[str] = []
    if response is None:
        return False, ["No response produced."]
    if response.get("decisionId") != pd.get("decisionId"):
        notes.append(
            f"decisionId mismatch: expected {pd.get('decisionId')!r}, "
            f"got {response.get('decisionId')!r}"
        )

    kind = pd.get("kind")
    rtype = response.get("type")

    if kind == "DISTRIBUTE":
        if rtype != "DistributionResponse":
            notes.append(f"DISTRIBUTE expects DistributionResponse, got {rtype!r}")
        distribution = response.get("distribution")
        if not isinstance(distribution, dict):
            notes.append(
                f"distribution must be a JSON object mapping entityId→int, "
                f"got {type(distribution).__name__}"
            )
            distribution = {}
        total_required = (pd.get("shape") or {}).get("totalToDistribute")
        valid_ids = {dt["target"]["entityId"] for dt in pd.get("distributionTargets") or []}
        unknown = [k for k in distribution if not isinstance(k, str) or k not in valid_ids]
        if unknown:
            notes.append(f"distribution references unknown entityIds: {unknown}")
        if not all(isinstance(v, int) and v >= 0 for v in distribution.values()):
            notes.append(
                "distribution values must be non-negative ints; "
                f"got {[type(v).__name__ for v in distribution.values()]}"
            )
        if total_required is not None:
            total = sum(int(v) for v in distribution.values() if isinstance(v, int))
            if total != total_required and not pd.get("allowPartial"):
                notes.append(f"distribution total {total} != totalToDistribute {total_required}")
        min_per_target = pd.get("minPerTarget") or 0
        if min_per_target:
            short = [
                k for k, v in distribution.items() if isinstance(v, int) and 0 < v < min_per_target
            ]
            if short:
                notes.append(f"distribution targets below minPerTarget={min_per_target}: {short}")
        shape = pd.get("shape") or {}
        max_sel = shape.get("maxSelections") or 0
        nonzero = [k for k, v in distribution.items() if isinstance(v, int) and v > 0]
        if max_sel and len(nonzero) > max_sel:
            notes.append(f"distribution touches {len(nonzero)} targets; maxSelections={max_sel}")
        min_sel = shape.get("minSelections") or 0
        if min_sel and len(nonzero) < min_sel:
            notes.append(f"distribution touches {len(nonzero)} targets; minSelections={min_sel}")

    elif kind in {"SELECT_CARDS", "SEARCH_LIBRARY"}:
        if rtype != "CardsSelectedResponse":
            notes.append(f"{kind} expects CardsSelectedResponse, got {rtype!r}")
        selected = response.get("selectedCards") or []
        valid_ids = {opt["entityId"] for opt in pd.get("options") or []}
        unknown = [c for c in selected if c not in valid_ids]
        if unknown:
            notes.append(f"selectedCards references unknown entityIds: {unknown}")
        shape = pd.get("shape") or {}
        min_sel = shape.get("minSelections") or 0
        max_sel = shape.get("maxSelections") or len(valid_ids)
        if len(selected) < min_sel:
            notes.append(f"selectedCards count {len(selected)} < minSelections {min_sel}")
        if len(selected) > max_sel:
            notes.append(f"selectedCards count {len(selected)} > maxSelections {max_sel}")
        if len(set(selected)) != len(selected):
            notes.append("selectedCards contains duplicates")

    elif kind == "CHOOSE_TARGETS":
        if rtype != "TargetsResponse":
            notes.append(f"CHOOSE_TARGETS expects TargetsResponse, got {rtype!r}")
        selected = response.get("selectedTargets") or {}
        legal = pd.get("legalTargets") or {}
        for req in pd.get("targetRequirements") or []:
            idx = str(req.get("index"))
            picked = selected.get(idx) or selected.get(int(idx)) or []
            allowed = {t["entityId"] for t in (legal.get(idx) or legal.get(int(idx)) or [])}
            bad = [t for t in picked if t not in allowed]
            if bad:
                notes.append(f"target slot {idx} chose illegal targets: {bad}")
            min_t, max_t = req.get("minTargets", 0), req.get("maxTargets", 0)
            if len(picked) < min_t:
                notes.append(f"target slot {idx} count {len(picked)} < minTargets {min_t}")
            if max_t and len(picked) > max_t:
                notes.append(f"target slot {idx} count {len(picked)} > maxTargets {max_t}")

    elif kind == "CHOOSE_MODE":
        if rtype != "ModesChosenResponse":
            notes.append(f"CHOOSE_MODE expects ModesChosenResponse, got {rtype!r}")
        selected = response.get("selectedModes") or []
        valid_idx = {m["index"] for m in pd.get("modes") or [] if m.get("available", True)}
        bad = [m for m in selected if m not in valid_idx]
        if bad:
            notes.append(f"selectedModes references unavailable modes: {bad}")

    else:
        notes.append(f"validator does not know kind={kind!r}; treating as accepted")

    return (not notes), notes


def _capture_ollama_reflection(agent: PlayerAgent, *, verbose: bool) -> str:
    """Append the meta-reflection prompt; capture the next assistant message."""
    response = agent.agent.run(REFLECTION_PROMPT, verbose=verbose)
    return (response.content or "").strip()


def _write_reflection(
    *,
    fixture_path: Path,
    model: str,
    response: dict | None,
    raw_model_response: dict | None,
    used_fallback: bool,
    valid: bool,
    notes: list[str],
    reasoning: str,
    reflection: str,
) -> Path:
    reflections_dir = fixture_path.parent / "reflections"
    reflections_dir.mkdir(parents=True, exist_ok=True)
    safe_model = model.replace("/", "_").replace(":", "_")
    out = reflections_dir / f"{fixture_path.stem}__{safe_model}.md"
    timestamp = _dt.datetime.now(tz=_dt.UTC).isoformat(timespec="seconds")
    body = [
        f"# {fixture_path.stem} — {model}",
        f"_Captured {timestamp}_",
        "",
        f"**Validated:** {'PASS' if valid else 'FAIL'}"
        + ("  (PlayerAgent fallback used)" if used_fallback else ""),
    ]
    if notes:
        body.append("")
        body.append("**Validation notes:**")
        body.extend(f"- {n}" for n in notes)
    body += [
        "",
        "## Model emission (raw, via submit_decision)",
        "```json",
        json.dumps(raw_model_response, indent=2, sort_keys=True)
        if raw_model_response is not None
        else "// model never called submit_decision",
        "```",
    ]
    if used_fallback:
        body += [
            "",
            "## PlayerAgent fallback response (what the engine actually saw)",
            "```json",
            json.dumps(response, indent=2, sort_keys=True) if response else "null",
            "```",
        ]
    body += [
        "",
        "## In-character reasoning attached to the decision",
        reasoning or "_(none)_",
        "",
        "## Out-of-character reflection",
        reflection or "_(none)_",
        "",
    ]
    out.write_text("\n".join(body))
    return out


def _opponent_name(obs: dict, perspective_name: str) -> str:
    for p in obs.get("players", []):
        if p.get("name") != perspective_name:
            return p.get("name", "Opponent")
    return "Opponent"


def _deck_for_perspective(name: str) -> str:
    return {
        "aria": "red_bolt",
        "noct": "blue_tempo",
        "mira": "white_aegis",
        "bryn": "green_might",
    }.get(name, "white_aegis")


class _NullEventLog:
    """In-memory event log: PlayerAgent expects an EventLog but we discard writes."""

    def __init__(self, fixture_path: Path) -> None:
        self.fixture_path = fixture_path
        self.events: list[tuple[str, dict]] = []

    def append(self, kind: str, payload: dict) -> None:
        self.events.append((kind, payload))

    def set_context(self, **_: Any) -> None:
        return

    def close(self) -> None:
        return


def summarize(results: Iterable[FixtureResult]) -> str:
    results = list(results)
    if not results:
        return "no fixtures run"
    lines = []
    pass_count = sum(1 for r in results if r.valid)
    lines.append(f"{pass_count}/{len(results)} valid")
    for r in results:
        status = "PASS" if r.valid else "FAIL"
        fb = " (fallback)" if r.used_fallback else ""
        lines.append(f"  [{status}] {r.kind:<16s} {r.fixture_path.name}{fb}")
        for note in r.validation_notes:
            lines.append(f"          ! {note}")
        if r.reflection_path:
            lines.append(f"          reflection → {r.reflection_path}")
    return "\n".join(lines)

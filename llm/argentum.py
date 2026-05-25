"""HTTP client for the Argentum Engine gym-server."""

from __future__ import annotations

import os

import requests

ARGENTUM_HOST = os.environ.get("ARGENTUM_HOST", "http://localhost:8081")
_SESSION = requests.Session()


def create_env(
    player_a: str,
    deck_a: dict[str, int],
    player_b: str,
    deck_b: dict[str, int],
    perspective_index: int = 0,
    reveal_all: bool = True,
    skip_mulligans: bool = False,
) -> tuple[str, dict]:
    """Create a new game env. Returns (env_id, opening_observation)."""
    payload = {
        "players": [
            {"name": player_a, "deck": {"type": "Explicit", "cards": deck_a}},
            {"name": player_b, "deck": {"type": "Explicit", "cards": deck_b}},
        ],
        "skipMulligans": skip_mulligans,
        "perspectivePlayerIndex": perspective_index,
        "revealAll": reveal_all,
    }
    r = _SESSION.post(f"{ARGENTUM_HOST}/envs", json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data["envId"], data["observation"]


def observe(env_id: str, reveal_all: bool = True) -> dict:
    params = {"revealAll": "true" if reveal_all else "false"}
    r = _SESSION.get(f"{ARGENTUM_HOST}/envs/{env_id}", params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def step(env_id: str, action_id: int) -> dict:
    r = _SESSION.post(
        f"{ARGENTUM_HOST}/envs/{env_id}/step",
        json={"actionId": action_id},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def advance(
    env_id: str,
    action_id: int,
    auto_resolve_decisions: bool = True,
    x_value: int | None = None,
    damage_distribution: dict[str, int] | None = None,
) -> dict:
    """Advance via the richer harness endpoint.

    Returns `{observation, submittedAction, events}`. The existing `step`
    wrapper intentionally stays on `/step` for raw gym compatibility.

    `x_value` should be set whenever the chosen legal action has
    `hasXCost=true`; without it the gym's enumerator leaves X=0 and the
    spell resolves as a no-op.

    `damage_distribution` should be set whenever the chosen legal action
    has `requiresDamageDistribution=true`. Keys are target EntityId strings,
    values are the damage assigned to each; the sum should equal
    `totalDamageToDistribute`. Without it the spell deals 0 to every target.
    """
    payload: dict = {
        "actionId": action_id,
        "autoResolveDecisions": auto_resolve_decisions,
    }
    if x_value is not None:
        payload["xValue"] = x_value
    if damage_distribution is not None:
        payload["damageDistribution"] = damage_distribution
    r = _SESSION.post(
        f"{ARGENTUM_HOST}/envs/{env_id}/advance",
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def submit_decision(
    env_id: str,
    response: dict,
    auto_resolve_decisions: bool = True,
) -> dict:
    """Submit a structured DecisionResponse through the rich harness endpoint."""
    r = _SESSION.post(
        f"{ARGENTUM_HOST}/envs/{env_id}/decision/advance",
        json={
            "response": response,
            "autoResolveDecisions": auto_resolve_decisions,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def cast_overrides_for(action: dict) -> tuple[int | None, dict[str, int] | None]:
    """Derive (xValue, damageDistribution) defaults from a legal action.

    Argentum's enumerator leaves CastSpell.xValue=null and
    CastSpell.damageDistribution=null for X-cost and divided-damage spells,
    so without overrides those spells resolve as 0-damage no-ops. The
    harness picks sensible defaults here:

    - X-cost: maximum X the caster can afford (`maxAffordableX`). The caller
      can override per-decision (e.g. the LLM picks a smaller X). For
      symmetric spells like Prosperity this can hurt — that's a known
      trade-off; LLM control closes the loop.
    - Divided damage: distribute `totalDamageToDistribute` across the
      target list as evenly as possible, respecting `minDamagePerTarget`.

    Returns (None, None) for actions that don't need either override.
    """
    x_value: int | None = None
    distribution: dict[str, int] | None = None

    if action.get("hasXCost"):
        max_x = action.get("maxAffordableX")
        min_x = action.get("minX", 0) or 0
        if isinstance(max_x, int):
            x_value = max(min_x, max_x)
        elif isinstance(min_x, int) and min_x > 0:
            x_value = min_x

    if action.get("requiresDamageDistribution"):
        total = int(action.get("totalDamageToDistribute") or 0)
        per_target_min = int(action.get("minDamagePerTarget") or 1)
        max_targets = int(action.get("maxTargets") or 0) or 1
        min_targets = int(action.get("minTargets") or 1)
        candidates = list(action.get("targetEntityIds") or [])
        if total > 0 and candidates:
            # `targetEntityIds` on the legal action is the candidate POOL, not
            # the chosen targets. Pick as many as we can fit, capped by both
            # `maxTargets` and the budget allowed by `minDamagePerTarget`
            # (e.g. Forked Lightning: total 4, min 1, so at most 4 targets;
            # capped further by maxTargets=3 → pick 3).
            budget_cap = total // max(per_target_min, 1)
            n = min(len(candidates), max_targets, budget_cap)
            n = max(n, min_targets)  # at least the minimum required
            picked = candidates[:n]
            base = total // n
            rem = total % n
            distribution = {}
            for i, entity_id in enumerate(picked):
                amt = base + (1 if i < rem else 0)
                if amt < per_target_min:
                    amt = per_target_min
                distribution[str(entity_id)] = amt

    return x_value, distribution


def dispose(env_id: str) -> None:
    _SESSION.delete(f"{ARGENTUM_HOST}/envs", json={"envIds": [env_id]})


def health() -> bool:
    try:
        r = _SESSION.get(f"{ARGENTUM_HOST}/health", timeout=5)
        return r.status_code == 200
    except requests.RequestException:
        return False

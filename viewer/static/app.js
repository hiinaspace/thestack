/* The Stack — replay viewer SPA. */

const state = {
  gameId: null,
  events: [],            // raw event list
  observations: [],      // ALL observation indices into events[]
  scrubObservations: [], // observation indices the scrubber stops on
  personas: {},          // {persona_name: {filestem: text}}
  oracle: {},            // {cardName: {oracle_text, type_line, mana_cost, ...}}
  step: 0,               // index into scrubObservations[]
  activeTab: null,
  showAllSteps: false,   // toggle: include autopass-only observations
};

window.thestackOracle = state.oracle;

// ---------------------------------------------------------------- DOM refs

const $picker = document.getElementById("game-picker");
const $summary = document.getElementById("game-summary");
const $scrubber = document.getElementById("scrubber");
const $stepLabel = document.getElementById("step-label");
const $stateLabel = document.getElementById("state-label");
const $board = document.getElementById("board-panel");
const $tabs = document.getElementById("tabs");
const $tabBody = document.getElementById("tab-body");
const $prev = document.getElementById("prev");
const $next = document.getElementById("next");
const $showAll = document.getElementById("show-all");

// ------------------------------------------------------------------ events

$picker.addEventListener("change", () => loadGame($picker.value));
$scrubber.addEventListener("input", (e) => setStep(parseInt(e.target.value, 10)));
$prev.addEventListener("click", () => setStep(state.step - 1));
$next.addEventListener("click", () => setStep(state.step + 1));
$showAll.addEventListener("change", () => {
  state.showAllSteps = $showAll.checked;
  // Map the current obs index onto the new list, then re-clamp.
  const curObsIdx = currentObsIndex();
  rebuildScrubObservations();
  $scrubber.max = Math.max(0, state.scrubObservations.length - 1);
  const newStep = state.scrubObservations.indexOf(curObsIdx);
  setStep(newStep >= 0 ? newStep : 0);
  $summary.textContent =
    `${state.events.length} events · ${state.observations.length} board states` +
    ` · scrubbing ${state.scrubObservations.length}`;
});
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "SELECT" || e.target.tagName === "INPUT") return;
  if (e.key === "ArrowLeft") setStep(state.step - 1);
  else if (e.key === "ArrowRight") setStep(state.step + 1);
});

// -------------------------------------------------------------- bootstrap

(async function init() {
  // Fetch the oracle lookup once — used by board.js to enrich tooltips when
  // the obs's own oracleText is empty (most non-land cards).
  try {
    const oracle = await fetch("/api/oracle").then((r) => r.json());
    Object.assign(state.oracle, oracle);
  } catch (_e) { /* viewer still works without it */ }

  const list = await fetch("/api/games").then((r) => r.json());
  if (list.length === 0) {
    $summary.textContent = "no games found in games/";
    return;
  }
  for (const g of list) {
    const opt = document.createElement("option");
    opt.value = g.game_id;
    const players = g.players.join(" vs ") || "?";
    const winner = g.winner ? `winner: ${g.winner}` : (g.stop_reason || "in-progress");
    opt.textContent = `${players}  ·  T${g.turns}  ·  ${winner}  ·  ${g.game_id.slice(0, 8)}`;
    $picker.appendChild(opt);
  }
  const params = new URLSearchParams(window.location.search);
  const wanted = params.get("game");
  const initial = wanted && list.some((g) => g.game_id === wanted) ? wanted : list[0].game_id;
  $picker.value = initial;
  await loadGame(initial);
  const stepParam = parseInt(params.get("step") || "0", 10);
  if (Number.isFinite(stepParam)) setStep(stepParam);
})();

// ---------------------------------------------------------------- game I/O

async function loadGame(gameId) {
  state.gameId = gameId;
  const [gd, pd] = await Promise.all([
    fetch(`/api/games/${gameId}`).then((r) => r.json()),
    fetch(`/api/games/${gameId}/personas`).then((r) => (r.ok ? r.json() : {})),
  ]);
  state.events = gd.events;
  state.observations = [];
  for (let i = 0; i < state.events.length; i++) {
    if (state.events[i].event === "observation") state.observations.push(i);
  }
  rebuildScrubObservations();
  state.personas = pd;
  state.step = 0;

  buildTabs();
  $scrubber.max = Math.max(0, state.scrubObservations.length - 1);
  $scrubber.value = 0;
  setStep(0);
  $summary.textContent =
    `${state.events.length} events · ${state.observations.length} board states` +
    ` · scrubbing ${state.scrubObservations.length}`;
}

/**
 * Build the list of observation indices the scrubber stops on. Skip observations
 * whose preceding action was an autopass — those are the "and now both bots
 * passed through the upkeep" beats nobody wants to scrub through. Always
 * include the first and last observation.
 */
function rebuildScrubObservations() {
  if (state.showAllSteps) {
    state.scrubObservations = [...state.observations];
    return;
  }
  const keep = [];
  for (let k = 0; k < state.observations.length; k++) {
    const obsIdx = state.observations[k];
    const isFirst = k === 0;
    const isLast = k === state.observations.length - 1;
    if (isFirst || isLast) {
      keep.push(obsIdx);
      continue;
    }
    // Look backwards from obsIdx for the action that produced it; keep iff
    // that action wasn't autopass.
    let producedByAutopass = false;
    for (let j = obsIdx - 1; j >= 0; j--) {
      const ev = state.events[j];
      if (ev.event === "action") {
        producedByAutopass = (ev.reasoning || "").startsWith("[autopass]");
        break;
      }
      if (ev.event === "observation") break;
    }
    if (!producedByAutopass) keep.push(obsIdx);
  }
  state.scrubObservations = keep;
}

// ------------------------------------------------------------------- tabs

function buildTabs() {
  $tabs.replaceChildren();
  const playerNames = Object.keys(state.personas);
  const tabs = [];
  for (const name of playerNames) tabs.push({ id: `thoughts:${name}`, label: `${name}'s thoughts` });
  tabs.push({ id: "commentary", label: "commentary" });
  tabs.push({ id: "all-events", label: "all events" });
  for (const name of playerNames) tabs.push({ id: `memory:${name}`, label: `${name}'s memory` });

  for (const t of tabs) {
    const btn = document.createElement("button");
    btn.textContent = t.label;
    btn.dataset.id = t.id;
    btn.addEventListener("click", () => activateTab(t.id));
    $tabs.appendChild(btn);
  }
  activateTab(tabs[0]?.id);
}

function activateTab(id) {
  state.activeTab = id;
  for (const b of $tabs.children) b.classList.toggle("active", b.dataset.id === id);
  renderTab();
}

// ------------------------------------------------------------------- step

function currentObsIndex() {
  return state.scrubObservations[state.step];
}

function currentObs() {
  const i = currentObsIndex();
  return i != null ? state.events[i].obs : null;
}

function setStep(n) {
  if (state.scrubObservations.length === 0) return;
  n = Math.max(0, Math.min(n, state.scrubObservations.length - 1));
  state.step = n;
  $scrubber.value = n;
  $stepLabel.textContent = `${n + 1} / ${state.scrubObservations.length}`;
  const obs = currentObs();
  const turn = obs?.turnNumber ?? "?";
  const phase = obs?.phase ?? "?";
  const step = obs?.step ?? "?";
  const term = obs?.terminated ? "  ·  GAME OVER" : "";
  $stateLabel.textContent = `Turn ${turn}  ·  ${phase}/${step}${term}`;
  renderBoard($board, obs);
  renderTab();
  // Mirror current step into the URL for deep-linking / refresh-safe state.
  const params = new URLSearchParams(window.location.search);
  if (state.gameId) params.set("game", state.gameId);
  params.set("step", String(n));
  const url = `${window.location.pathname}?${params.toString()}`;
  window.history.replaceState(null, "", url);
}

// --------------------------------------------------------------- tab body

function renderTab() {
  $tabBody.replaceChildren();
  const tab = state.activeTab;
  if (!tab) return;

  if (tab.startsWith("memory:")) {
    const persona = tab.slice("memory:".length);
    renderMemoryTab(persona);
    return;
  }

  if (tab === "all-events") {
    const filtered = state.events
      .slice(0, currentObsIndex() + 1)
      .filter((e) => state.showAllSteps || !isAutopassNoise(e));
    renderEventStream(filtered);
    return;
  }

  if (tab === "commentary") {
    const filtered = state.events
      .slice(0, currentObsIndex() + 1)
      .filter((e) => e.event === "commentary");
    renderEventStream(filtered);
    return;
  }

  if (tab.startsWith("thoughts:")) {
    const persona = tab.slice("thoughts:".length);
    const filtered = state.events
      .slice(0, currentObsIndex() + 1)
      .filter((e) => {
        if (!["reasoning", "thinking", "tool_call", "action"].includes(e.event)) return false;
        if (e.player !== persona) return false;
        if (!state.showAllSteps && isAutopassNoise(e)) return false;
        return true;
      });
    renderEventStream(filtered);
    return;
  }
}

function isAutopassNoise(e) {
  if (e.event === "autopass") return true;
  if (e.event === "action" && (e.reasoning || "").startsWith("[autopass]")) return true;
  return false;
}

function renderMemoryTab(persona) {
  const docs = state.personas[persona] || {};
  const order = ["identity", "strategy", "opponents", "memory"];
  for (const stem of order) {
    const body = docs[stem];
    if (!body) continue;
    const h = document.createElement("h3");
    h.textContent = `${stem}.md`;
    h.style.color = "var(--accent)";
    h.style.fontSize = "12px";
    h.style.textTransform = "uppercase";
    h.style.letterSpacing = "0.08em";
    h.style.marginTop = "16px";
    $tabBody.appendChild(h);
    const pre = document.createElement("div");
    pre.className = "memory-doc";
    pre.textContent = body;
    $tabBody.appendChild(pre);
  }
}

function renderEventStream(events) {
  if (events.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "(nothing yet at this point in the game)";
    $tabBody.appendChild(empty);
    return;
  }
  for (const e of events) {
    $tabBody.appendChild(eventEl(e));
  }
}

function eventEl(e) {
  const el = document.createElement("div");
  el.className = `event ${e.event}`;
  const head = document.createElement("div");
  head.className = "event-head";
  const who = e.player ? `${e.player} · ` : "";
  const turn = e.turn != null ? `T${e.turn} · ` : "";
  head.textContent = `${turn}${who}${e.event}`;
  el.appendChild(head);

  const body = document.createElement("div");
  body.className = "event-body";

  switch (e.event) {
    case "reasoning":
    case "thinking":
      body.textContent = e.text || "";
      break;
    case "tool_call":
      body.textContent = `${e.tool}(${JSON.stringify(e.args)})  →  ${e.result}`;
      break;
    case "action":
      body.textContent = `${e.description} — ${e.reasoning || ""}`;
      break;
    case "commentary":
      body.textContent = e.text || "";
      break;
    case "info":
      body.textContent = e.kind ? `[${e.kind}] ${e.text || ""}` : JSON.stringify(e);
      break;
    case "game_over":
      body.textContent = `winner: ${e.winner || "draw"} · reason: ${e.reason}`;
      break;
    default:
      body.textContent = JSON.stringify(e);
  }
  el.appendChild(body);
  return el;
}

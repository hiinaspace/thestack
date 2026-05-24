/* The Stack — replay viewer SPA. */

const state = {
  gameId: null,
  events: [],            // raw event list
  observations: [],      // ALL observation indices into events[]
  scrubObservations: [], // observation indices the scrubber stops on
  playersById: {},       // stable player id -> display name for event summaries
  playerOrder: [],       // display order from the first observation
  cardNames: [],         // canonical names for rich text card hovers
  initialDecks: {},      // player name -> counted full deck from first observation
  personas: {},          // {persona_name: {filestem: text}}
  oracle: {},            // {cardName: {oracle_text, type_line, mana_cost, ...}}
  step: 0,               // index into scrubObservations[]
  activeTab: null,
  showAllSteps: false,   // toggle: include autopass-only observations
};

window.thestackOracle = state.oracle;
window.thestackDecks = state.initialDecks;
window.openDeckModal = openDeckModal;

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
const $deckModal = document.getElementById("deck-modal");
const $deckModalTitle = document.getElementById("deck-modal-title");
const $deckModalMeta = document.getElementById("deck-modal-meta");
const $deckModalBody = document.getElementById("deck-modal-body");
const $deckModalClose = document.getElementById("deck-modal-close");

let activeThoughtTooltip = null;
let thoughtHideTimer = null;

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
$deckModalClose?.addEventListener("click", closeDeckModal);
$deckModal?.addEventListener("click", (e) => {
  if (e.target.dataset.closeModal === "deck") closeDeckModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeDeckModal();
  if ($deckModal && !$deckModal.classList.contains("hidden")) return;
  if (e.target.tagName === "SELECT" || e.target.tagName === "INPUT") return;
  if (e.key === "ArrowLeft") setStep(state.step - 1);
  else if (e.key === "ArrowRight") setStep(state.step + 1);
});
window.addEventListener("resize", hideThoughtTooltip);
$tabBody.addEventListener("scroll", hideThoughtTooltip);

// -------------------------------------------------------------- bootstrap

(async function init() {
  // Fetch the oracle lookup once — used by board.js to enrich tooltips when
  // the obs's own oracleText is empty (most non-land cards).
  try {
    const oracle = await fetch("/api/oracle").then((r) => r.json());
    mergeCardNames(Object.keys(oracle));
    Object.assign(state.oracle, oracle);
    for (const [name, card] of Object.entries(oracle)) {
      state.oracle[name.toLowerCase()] = card;
    }
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
  state.playersById = {};
  state.playerOrder = [];
  state.initialDecks = {};
  for (let i = 0; i < state.events.length; i++) {
    if (state.events[i].event === "observation") {
      state.observations.push(i);
      for (const p of state.events[i].obs?.players || []) {
        state.playersById[p.id] = p.name;
        if (!state.playerOrder.includes(p.name)) state.playerOrder.push(p.name);
      }
    }
  }
  state.initialDecks = collectInitialDecks(state.events);
  window.thestackDecks = state.initialDecks;
  mergeCardNames(collectCardNamesFromEvents(state.events));
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
  tabs.push({ id: "timeline", label: "timeline" });
  for (const name of playerNames) tabs.push({ id: `thoughts:${name}`, label: `${name}'s thoughts` });
  tabs.push({ id: "commentary", label: "commentary" });
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

function currentEventCutoff() {
  const i = currentObsIndex();
  if (i == null) return 0;
  let cutoff = i + 1;
  while (cutoff < state.events.length && state.events[cutoff].event !== "observation") {
    if (isEventForNextBoardState(state.events[cutoff])) break;
    cutoff += 1;
  }
  return cutoff;
}

function isEventForNextBoardState(e) {
  return ["autopass", "thinking", "tool_call", "action", "engine_event", "reasoning"].includes(
    e.event
  );
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

// --------------------------------------------------------------- deck modal

function openDeckModal(player, zones = {}) {
  if (!$deckModal || !$deckModalBody) return;
  const deck = state.initialDecks[player.name] || countCardsFromZones(zones);
  const library = zones.Library || [];

  $deckModalTitle.textContent = `${player.name} — library and deck`;
  $deckModalMeta.textContent =
    `${library.length} cards currently in library · ${deck.total} cards in original deck`;
  $deckModalBody.replaceChildren();

  const librarySection = document.createElement("section");
  librarySection.className = "deck-modal-section";
  const libraryTitle = document.createElement("h3");
  libraryTitle.textContent = "Current library (top first)";
  librarySection.appendChild(libraryTitle);
  librarySection.appendChild(libraryListEl(library));

  const deckSection = document.createElement("section");
  deckSection.className = "deck-modal-section";
  const deckTitle = document.createElement("h3");
  deckTitle.textContent = "Full decklist";
  deckSection.appendChild(deckTitle);
  deckSection.appendChild(deckCountListEl(deck.cards));

  $deckModalBody.appendChild(librarySection);
  $deckModalBody.appendChild(deckSection);
  $deckModal.classList.remove("hidden");
}

function closeDeckModal() {
  if (!$deckModal || $deckModal.classList.contains("hidden")) return;
  $deckModal.classList.add("hidden");
  $deckModalBody?.replaceChildren();
}

function libraryListEl(cards) {
  const list = document.createElement("ol");
  list.className = "deck-card-list deck-library-list";
  if (!cards.length) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "(empty)";
    list.appendChild(empty);
    return list;
  }
  for (const card of cards) {
    const item = document.createElement("li");
    item.className = "deck-card-row";
    appendCardToken(item, cardDisplayName(card));
    list.appendChild(item);
  }
  return list;
}

function deckCountListEl(cards) {
  const list = document.createElement("div");
  list.className = "deck-card-list deck-count-list";
  if (!cards.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "(no deck data)";
    list.appendChild(empty);
    return list;
  }
  for (const row of cards) {
    const item = document.createElement("div");
    item.className = "deck-card-row";
    const count = document.createElement("span");
    count.className = "deck-card-count";
    count.textContent = `${row.count}x`;
    item.appendChild(count);
    appendCardToken(item, row.name);
    const oracle = state.oracle[row.name] || state.oracle[row.name.toLowerCase()];
    const type = oracle?.type_line || row.sample?.types?.join(" ");
    if (type) {
      const typeEl = document.createElement("span");
      typeEl.className = "deck-card-type";
      typeEl.textContent = type;
      item.appendChild(typeEl);
    }
    list.appendChild(item);
  }
  return list;
}

function appendCardToken(parent, name) {
  const token = document.createElement("span");
  token.className = "card-text-token";
  token.textContent = name || "?";
  if (typeof window.attachCardHover === "function") {
    window.attachCardHover(token, { name });
  }
  parent.appendChild(token);
}

// --------------------------------------------------------------- tab body

function renderTab() {
  hideThoughtTooltip();
  $tabBody.replaceChildren();
  const tab = state.activeTab;
  if (!tab) return;

  if (tab.startsWith("memory:")) {
    const persona = tab.slice("memory:".length);
    renderMemoryTab(persona);
    return;
  }

  if (tab === "timeline") {
    renderTimeline();
    return;
  }

  if (tab === "commentary") {
    const filtered = state.events
      .slice(0, currentEventCutoff())
      .filter((e) => e.event === "commentary");
    renderEventStream(filtered);
    return;
  }

  if (tab.startsWith("thoughts:")) {
    const persona = tab.slice("thoughts:".length);
    const filtered = state.events
      .slice(0, currentEventCutoff())
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

function renderTimeline() {
  const cutoff = currentEventCutoff();
  const items = buildTimelineItems(state.events, cutoff);
  if (items.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "(no notable actions yet)";
    $tabBody.appendChild(empty);
    return;
  }
  for (const item of items) $tabBody.appendChild(timelineItemEl(item));
}

function buildTimelineItems(events, cutoff) {
  const items = [];
  const pendingThoughts = {};
  for (let i = 0; i < events.length; i++) {
    const e = events[i];
    if (e.event === "thinking" && e.player) {
      if (!pendingThoughts[e.player]) pendingThoughts[e.player] = [];
      pendingThoughts[e.player].push(e.text || "");
      pendingThoughts[e.player] = pendingThoughts[e.player].slice(-3);
      continue;
    }

    if (e.event === "action") {
      const engineEvents = followingEngineEvents(events, i, e);
      const result = summarizeEngineEvents(engineEvents);
      const passNoise = isAutopassNoise(e);
      if (passNoise && !result) continue;
      if (passNoise) {
        items.push(timelineItem({ kind: "result", event: e, result, engineEvents }, i, cutoff));
        continue;
      }
      items.push(
        timelineItem(
          {
            kind: "action",
            event: e,
            result,
            engineEvents,
            thinking: (pendingThoughts[e.player] || []).join("\n\n"),
          },
          i,
          cutoff
        )
      );
      pendingThoughts[e.player] = [];
      continue;
    }

    if (e.event === "commentary") {
      items.push(timelineItem({ kind: "commentary", event: e }, i, cutoff));
    } else if (e.event === "info" && e.kind === "post_game_reflection") {
      items.push(timelineItem({ kind: "reflection", event: e }, i, cutoff));
    } else if (e.event === "game_over") {
      items.push(timelineItem({ kind: "game_over", event: e }, i, cutoff));
    }
  }
  return items;
}

function timelineItem(item, eventIndex, cutoff) {
  item.eventIndex = eventIndex;
  item.future = eventIndex >= cutoff;
  return item;
}

function followingEngineEvents(events, actionIndex, action) {
  for (let j = actionIndex + 1; j < events.length; j++) {
    const e = events[j];
    if (e.event === "observation" || e.event === "action") return [];
    if (
      e.event === "engine_event" &&
      e.player === action.player &&
      e.action_id === action.action_id
    ) {
      return e.events || [];
    }
  }
  return [];
}

function timelineItemEl(item) {
  const el = document.createElement("div");
  el.className = `timeline-item timeline-${item.kind}`;
  if (item.future) el.classList.add("future");
  attachTimelineNavigation(el, item);

  if (item.kind === "commentary") {
    if (item.event.final) el.classList.add("timeline-final-commentary");
    const h = document.createElement("div");
    h.className = "timeline-head";
    h.textContent = item.event.final
      ? `T${item.event.turn} · final commentator`
      : `T${item.event.turn} · commentator`;
    el.appendChild(h);
    const body = document.createElement("div");
    body.className = "timeline-commentary";
    appendRichText(body, item.event.text || "");
    el.appendChild(body);
    return el;
  }

  if (item.kind === "reflection") {
    const h = document.createElement("div");
    h.className = "timeline-head";
    h.textContent = `T${item.event.turn} · ${item.event.player} · post-game`;
    el.appendChild(h);
    const body = document.createElement("div");
    body.className = "timeline-reflection";
    appendRichText(body, item.event.text || item.event.raw || "");
    el.appendChild(body);
    colorTimelineItem(el, item.event.player);
    return el;
  }

  if (item.kind === "game_over") {
    const h = document.createElement("div");
    h.className = "timeline-head";
    h.textContent = `T${item.event.turn} · game over`;
    el.appendChild(h);
    const body = document.createElement("div");
    body.className = "timeline-game-over";
    body.textContent = `winner: ${item.event.winner || "draw"} · reason: ${item.event.reason}`;
    el.appendChild(body);
    return el;
  }

  if (item.kind === "result") {
    colorTimelineItem(el, item.event.player);
    const h = document.createElement("div");
    h.className = "timeline-head";
    h.textContent = `T${item.event.turn} · result`;
    el.appendChild(h);
    const body = document.createElement("div");
    body.className = "timeline-result";
    appendRichText(body, item.result);
    el.appendChild(body);
    return el;
  }

  const e = item.event;
  colorTimelineItem(el, e.player);
  const h = document.createElement("div");
  h.className = "timeline-head";
  h.textContent = `T${e.turn} · ${e.player} · ${e.phase || ""}`;
  el.appendChild(h);

  const action = document.createElement("div");
  action.className = "timeline-action-title";
  appendRichText(action, e.description || `action ${e.action_id}`);
  el.appendChild(action);

  if (e.reasoning) {
    const reason = document.createElement("div");
    reason.className = "timeline-reason";
    appendRichText(reason, e.reasoning);
    el.appendChild(reason);
  }

  if (item.result) {
    const result = document.createElement("div");
    result.className = "timeline-result";
    appendRichText(result, item.result);
    el.appendChild(result);
  }

  if (item.thinking) {
    const thought = document.createElement("span");
    thought.className = "thought-trigger";
    thought.textContent = "thought";
    thought.addEventListener("mouseenter", (event) => showThoughtTooltip(item.thinking, event));
    thought.addEventListener("mousemove", positionThoughtTooltip);
    thought.addEventListener("mouseleave", scheduleHideThoughtTooltip);
    el.appendChild(thought);
  }

  return el;
}

function summarizeEngineEvents(events) {
  const buckets = {
    deaths: [],
    life: [],
    combat: [],
    board: [],
    game: [],
  };
  const noise = new Set([
    "PriorityChanged",
    "PhaseChanged",
    "StepChanged",
    "ManaAdded",
    "ManaSpent",
    "DecisionRequested",
    "DecisionSubmitted",
    "TurnChanged",
    "Untapped",
    "CommitCrime",
    "Resolved",
  ]);
  for (const ev of events || []) {
    const type = ev.type;
    if (noise.has(type)) continue;
    const text = publicEngineText(ev);
    if (!text) continue;
    if (type === "CreatureDestroyed") appendUnique(buckets.deaths, text);
    else if (type === "LifeChanged") appendUnique(buckets.life, text);
    else if (["AttackersDeclared", "BlockersDeclared", "DamageDealt"].includes(type)) {
      appendUnique(buckets.combat, text);
    } else if (["PlayerLost", "GameEnded"].includes(type)) appendUnique(buckets.game, text);
    else appendUnique(buckets.board, text);
  }

  const parts = [];
  for (const [label, texts] of Object.entries(buckets)) {
    if (texts.length) parts.push(`${label}: ${texts.slice(0, 5).join("; ")}`);
  }
  return parts.join(" | ");
}

function publicEngineText(ev) {
  const names = eventPlayerNames(ev);
  const who = names.length ? names.join(", ") : "A player";
  const card = ev.cardNames?.[0] || "A source";
  if (ev.type === "CardsDrawn") return `${who} drew ${ev.amount || "some"} card(s)`;
  if (ev.type === "LifeChanged" && ev.text) return `${who}: ${formatLifeChange(ev.text)}`;
  if (ev.type === "DamageDealt") {
    return `${card} dealt ${ev.amount || "?"} damage to ${eventDamageTarget(ev)}`;
  }
  if (ev.type === "BecomesTarget") return formatBecomesTarget(ev);
  if (ev.type === "Tapped") return `${card} tapped`;
  if (ev.type === "ZoneChange") return formatZoneChange(ev);
  if (ev.type === "PlayerLost") return `${eventPlayerFromRawText(ev.text) || who} lost`;
  return ev.text || "";
}

function eventPlayerNames(ev) {
  const names = [];
  for (const id of ev.playerIds || []) {
    const name = state.playersById[id];
    if (name && !names.includes(name)) names.push(name);
  }
  return names;
}

function eventDamageTarget(ev) {
  for (const id of (ev.entityIds || []).slice(1)) {
    if (state.playersById[id]) return state.playersById[id];
  }
  if (ev.cardNames?.length > 1) return ev.cardNames[1];
  const m = (ev.text || "").match(/\bto ([0-9a-f-]{36}|Player)$/i);
  if (m && state.playersById[m[1]]) return state.playersById[m[1]];
  return "player";
}

function eventPlayerFromRawText(text) {
  const m = (text || "").match(/playerId=([0-9a-f-]{36})/i);
  return m ? state.playersById[m[1]] : "";
}

function formatLifeChange(text) {
  const m = text.match(/Life changed (-?\d+) -> (-?\d+) \(([^)]+)\)/);
  return m ? `${m[1]} -> ${m[2]} (${m[3].toLowerCase()})` : text;
}

function formatZoneChange(ev) {
  const card = ev.cardNames?.[0] || "Card";
  const m = (ev.text || "").match(/^(.+?) moved from ([A-Z_]+) to ([A-Z_]+)$/);
  if (!m) return ev.text || "";
  const from = fmtZone(m[2]);
  const to = fmtZone(m[3]);
  if (m[3] === "BATTLEFIELD") return `${card} entered battlefield`;
  if (m[3] === "GRAVEYARD") return `${card} went to graveyard`;
  return `${card} moved ${from} -> ${to}`;
}

function formatBecomesTarget(ev) {
  const target = rawEventField(ev.text || "", "targetName");
  const source = rawEventField(ev.text || "", "sourceName");
  if (target && source) return `${target} became target of ${source}`;
  if (target) return `${target} became a target`;
  return "";
}

function rawEventField(text, field) {
  const re = new RegExp(`${field}=([^,)]+)`);
  const m = text.match(re);
  return m ? m[1].trim() : "";
}

function fmtZone(zone) {
  return zone.replace(/_/g, " ").toLowerCase();
}

function appendUnique(items, text) {
  if (!items.includes(text)) items.push(text);
}

function colorTimelineItem(el, player) {
  if (!player) return;
  el.style.setProperty("--timeline-player-color", playerColor(player));
}

function mergeCardNames(names) {
  const byLower = new Map();
  for (const name of [...state.cardNames, ...(names || [])]) {
    const clean = String(name || "").trim();
    if (!clean) continue;
    const key = clean.toLowerCase();
    if (!byLower.has(key)) byLower.set(key, clean);
  }
  state.cardNames = [...byLower.values()].sort(
    (a, b) => b.length - a.length || a.localeCompare(b)
  );
}

function collectCardNamesFromEvents(events) {
  const names = new Set();
  for (const e of events || []) {
    addCardNames(names, e.cardNames);
    for (const ev of e.events || []) addCardNames(names, ev.cardNames);
    collectCardNamesFromObservation(names, e.obs);
  }
  return [...names];
}

function collectInitialDecks(events) {
  const firstObs = (events || []).find((e) => e.event === "observation")?.obs;
  if (!firstObs) return {};
  const byPlayer = {};
  const playerNames = {};
  for (const p of firstObs.players || []) playerNames[p.id] = p.name;
  for (const zone of firstObs.zones || []) {
    const player = playerNames[zone.ownerId];
    if (!player || zone.hidden) continue;
    if (!byPlayer[player]) byPlayer[player] = [];
    byPlayer[player].push(...(zone.cards || []));
  }

  const decks = {};
  for (const [player, cards] of Object.entries(byPlayer)) {
    decks[player] = countCards(cards);
  }
  return decks;
}

function countCardsFromZones(zones) {
  const cards = [];
  for (const zoneCards of Object.values(zones || {})) {
    cards.push(...(zoneCards || []));
  }
  return countCards(cards);
}

function countCards(cards) {
  const rows = new Map();
  for (const card of cards || []) {
    const name = cardDisplayName(card);
    if (!name) continue;
    const row = rows.get(name) || { name, count: 0, sample: card };
    row.count += 1;
    rows.set(name, row);
  }
  const sorted = [...rows.values()].sort((a, b) => {
    const aLand = isLandName(a.name) ? 1 : 0;
    const bLand = isLandName(b.name) ? 1 : 0;
    if (aLand !== bLand) return aLand - bLand;
    return a.name.localeCompare(b.name);
  });
  const total = sorted.reduce((sum, row) => sum + row.count, 0);
  return { total, cards: sorted };
}

function isLandName(name) {
  const card = state.oracle[name] || state.oracle[name.toLowerCase()];
  return /land/i.test(card?.type_line || "");
}

function collectCardNamesFromObservation(names, obs) {
  if (!obs) return;
  for (const z of obs.zones || []) {
    for (const c of z.cards || []) addCardName(names, cardDisplayName(c));
  }
  for (const c of obs.stack || []) addCardName(names, cardDisplayName(c));
}

function addCardNames(names, cardNames) {
  for (const name of cardNames || []) addCardName(names, name);
}

function addCardName(names, name) {
  const clean = String(name || "").trim();
  if (clean) names.add(clean);
}

function cardDisplayName(card) {
  if (!card) return "";
  if (card.name) return card.name;
  return String(card.cardDefinitionId || "").split("#")[0];
}

function playerColor(player) {
  const names = state.playerOrder.length ? state.playerOrder : Object.values(state.playersById);
  const idx = names.indexOf(player);
  if (idx === 0) return "#5eead4";
  if (idx === 1) return "#fb7185";
  return "#f59e0b";
}

function attachTimelineNavigation(el, item) {
  const targetStep = stepForEventIndex(item.eventIndex);
  if (targetStep == null) return;
  el.classList.add("timeline-clickable");
  el.title = "Jump board to this point";
  el.addEventListener("click", (event) => {
    if (event.target.closest?.(".thought-trigger, .card-text-token")) return;
    const scrollTop = $tabBody.scrollTop;
    setStep(targetStep);
    requestAnimationFrame(() => {
      if (state.activeTab === "timeline") $tabBody.scrollTop = scrollTop;
    });
  });
  el.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    const scrollTop = $tabBody.scrollTop;
    setStep(targetStep);
    requestAnimationFrame(() => {
      if (state.activeTab === "timeline") $tabBody.scrollTop = scrollTop;
    });
  });
  el.tabIndex = 0;
}

function stepForEventIndex(eventIndex) {
  if (!state.scrubObservations.length) return null;
  let obsIdx = null;
  for (let i = eventIndex + 1; i < state.events.length; i++) {
    if (state.events[i].event === "observation") {
      obsIdx = i;
      break;
    }
  }
  if (obsIdx == null) {
    for (let i = eventIndex; i >= 0; i--) {
      if (state.events[i].event === "observation") {
        obsIdx = i;
        break;
      }
    }
  }
  if (obsIdx == null) return null;
  let step = state.scrubObservations.indexOf(obsIdx);
  if (step >= 0) return step;
  step = state.scrubObservations.findIndex((i) => i > obsIdx);
  return step >= 0 ? step : state.scrubObservations.length - 1;
}

function appendRichText(el, text) {
  const raw = String(text || "");
  let i = 0;
  while (i < raw.length) {
    const name = matchingCardName(raw, i);
    if (!name) {
      const next = nextCardNameIndex(raw, i + 1);
      el.appendChild(document.createTextNode(raw.slice(i, next)));
      i = next;
      continue;
    }
    const token = document.createElement("span");
    token.className = "card-text-token";
    token.textContent = name;
    if (typeof window.attachCardHover === "function") window.attachCardHover(token, { name });
    el.appendChild(token);
    i += name.length;
  }
}

function nextCardNameIndex(text, start) {
  let next = text.length;
  for (let i = start; i < text.length; i++) {
    if (matchingCardName(text, i)) return i;
  }
  return next;
}

function matchingCardName(text, index) {
  for (const name of state.cardNames) {
    if (text.slice(index, index + name.length).toLowerCase() !== name.toLowerCase()) continue;
    if (!isCardNameBoundary(text[index - 1]) || !isCardNameBoundary(text[index + name.length])) {
      continue;
    }
    return name;
  }
  return "";
}

function isCardNameBoundary(ch) {
  return !ch || !/[A-Za-z0-9]/.test(ch);
}

function showThoughtTooltip(text, event) {
  clearTimeout(thoughtHideTimer);
  if (!activeThoughtTooltip) {
    activeThoughtTooltip = document.createElement("div");
    activeThoughtTooltip.className = "thought-popover";
    activeThoughtTooltip.addEventListener("mouseenter", () => clearTimeout(thoughtHideTimer));
    activeThoughtTooltip.addEventListener("mouseleave", scheduleHideThoughtTooltip);
    document.body.appendChild(activeThoughtTooltip);
  }
  activeThoughtTooltip.textContent = text || "";
  activeThoughtTooltip.classList.add("visible");
  positionThoughtTooltip(event);
}

function positionThoughtTooltip(event) {
  if (!activeThoughtTooltip || !event) return;
  const gap = 14;
  const pad = 12;
  const rect = activeThoughtTooltip.getBoundingClientRect();
  const width = rect.width || 520;
  const height = rect.height || 420;
  let left = event.clientX - width - gap;
  if (left < pad) left = event.clientX + gap;
  left = Math.max(pad, Math.min(left, window.innerWidth - width - pad));
  let top = event.clientY - 28;
  top = Math.max(pad, Math.min(top, window.innerHeight - height - pad));
  activeThoughtTooltip.style.left = `${Math.round(left)}px`;
  activeThoughtTooltip.style.top = `${Math.round(top)}px`;
}

function scheduleHideThoughtTooltip() {
  clearTimeout(thoughtHideTimer);
  thoughtHideTimer = setTimeout(hideThoughtTooltip, 120);
}

function hideThoughtTooltip() {
  clearTimeout(thoughtHideTimer);
  if (!activeThoughtTooltip) return;
  activeThoughtTooltip.remove();
  activeThoughtTooltip = null;
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
    case "engine_event":
      body.textContent = (e.events || []).map((ev) => ev.text || ev.type).join("\n");
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

/* The Stack — Theater: a visual-novel presentation of a recorded game.
 *
 * Reuses board.js (cardEl / attachCardHover / getCardImageUrl) and gamedata.js
 * (loadGame / oracle / appendRichText / openDeckModal). The game is flattened
 * into an ordered "script" of narration beats; each beat is tied to the board
 * observation that was current when it happened. The viewer types each line out
 * character-by-character and lets you advance / rewind / scrub freely.
 */
(function () {
  "use strict";

  const D = window.TheStackData;

  const state = {
    gameId: null,
    events: [],
    playerOrder: [],
    playersById: {},
    script: [],        // ordered beats
    beatIndex: 0,
    shownObsIndex: null,
    typing: false,
    typeTimer: null,
    autoplay: false,
    autoTimer: null,
  };

  // ------------------------------------------------------------- DOM refs

  const $picker = document.getElementById("game-picker");
  const $stateLabel = document.getElementById("theater-state");
  const $matA = document.getElementById("mat-a");
  const $matB = document.getElementById("mat-b");
  const $narration = document.getElementById("narration");
  const $plate = document.getElementById("speaker-plate");
  const $text = document.getElementById("narration-text");
  const $advance = document.getElementById("advance-indicator");
  const $portraitLeft = document.getElementById("portrait-left");
  const $portraitRight = document.getElementById("portrait-right");
  const $scrubber = document.getElementById("beat-scrubber");
  const $beatLabel = document.getElementById("beat-label");
  const $prev = document.getElementById("beat-prev");
  const $next = document.getElementById("beat-next");
  const $autoplay = document.getElementById("autoplay-toggle");
  const $stage = document.getElementById("theater-stage");
  const $narrStage = document.getElementById("narration-stage");
  const $tracker = document.getElementById("turn-tracker");

  const MANA_COLORS = {
    white: "#f7f2d8",
    blue: "#5aa3e0",
    black: "#7a6b86",
    red: "#e0625a",
    green: "#54ad6a",
    colorless: "#b6bac8",
  };

  function playerColor(idx) {
    if (idx === 0) return "#5eead4";
    if (idx === 1) return "#fb7185";
    return "#f59e0b";
  }

  // ------------------------------------------------------------- bootstrap

  (async function init() {
    try {
      const oracle = await D.loadOracle();
      Object.assign(window.thestackOracle || (window.thestackOracle = {}), oracle);
    } catch (_e) { /* still works without it */ }

    D.initDeckModal();
    wireControls();

    const params = new URLSearchParams(window.location.search);
    const wanted = params.get("game");
    const beatParam = parseInt(params.get("beat") || "0", 10);
    const startBeat = Number.isFinite(beatParam) ? beatParam : 0;

    // Kick off the (potentially slow) game-list fetch, but don't block the first
    // render on it: if a specific game is requested, load + show it immediately.
    const listPromise = D.fetchGameList().catch(() => []);
    if (wanted) {
      try {
        await loadGame(wanted);
        showBeat(startBeat, { type: false });
      } catch (_e) { /* fall back to the list below */ }
    }

    const list = await listPromise;
    if (!list.length && !state.gameId) {
      $stateLabel.textContent = "no games found";
      return;
    }
    for (const g of list) {
      const opt = document.createElement("option");
      opt.value = g.game_id;
      const players = (g.players || []).join(" vs ") || "?";
      const winner = g.winner ? `winner: ${g.winner}` : (g.stop_reason || "in-progress");
      opt.textContent = `${players}  ·  T${g.turns}  ·  ${winner}  ·  ${g.game_id.slice(0, 8)}`;
      $picker.appendChild(opt);
    }

    const initial = wanted && list.some((g) => g.game_id === wanted)
      ? wanted
      : (state.gameId || list[0]?.game_id);
    if (initial) $picker.value = initial;
    if (initial && !state.gameId) {
      await loadGame(initial);
      showBeat(startBeat, { type: false });
    }
  })();

  function wireControls() {
    $picker.addEventListener("change", () => loadGame($picker.value).then(() => showBeat(0, { type: false })));
    $prev.addEventListener("click", manualPrev);
    $next.addEventListener("click", manualAdvance);
    // The continue arrow is the one click target that advances the story, so
    // clicking elsewhere (mats, dialogue) leaves text selectable.
    $advance.addEventListener("click", manualAdvance);
    $scrubber.addEventListener("input", (e) => { disableAutoplay(); showBeat(parseInt(e.target.value, 10), { type: false }); });
    $autoplay.addEventListener("click", toggleAutoplay);

    document.addEventListener("keydown", (e) => {
      if (D.deckModalOpen()) return;
      const tag = e.target.tagName;
      if (tag === "SELECT" || tag === "INPUT" || tag === "TEXTAREA") return;
      if (e.key === " " || e.key === "Enter" || e.key === "ArrowRight" || e.key === "ArrowDown") {
        e.preventDefault();
        manualAdvance();
      } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
        e.preventDefault();
        manualPrev();
      }
    });

    // Scroll wheel scrubs through beats (no typewriter), throttled.
    let wheelLock = 0;
    window.addEventListener(
      "wheel",
      (e) => {
        if (D.deckModalOpen()) return;
        const now = Date.now();
        if (now < wheelLock) return;
        wheelLock = now + 110;
        disableAutoplay();
        if (e.deltaY > 0) {
          if (state.typing) finishTyping();
          else showBeat(state.beatIndex + 1, { type: false });
        } else if (e.deltaY < 0) {
          showBeat(state.beatIndex - 1, { type: false });
        }
      },
      { passive: true }
    );
  }

  // Manual navigation always cancels auto-advance until the user re-enables it.
  function manualAdvance() { disableAutoplay(); advance(); }
  function manualPrev() { disableAutoplay(); prevBeat(); }

  // ------------------------------------------------------------- load game

  async function loadGame(gameId) {
    stopAutoplay();
    clearTyping();
    state.gameId = gameId;
    const data = await D.loadGame(gameId);
    state.events = data.events;
    state.playerOrder = data.playerOrder;
    state.playersById = data.playersById;
    window.thestackDecks = data.initialDecks;
    state.cardNames = D.sortCardNames([
      ...Object.keys(window.thestackOracle || {}),
      ...data.cardNames,
    ]);
    window.thestackCardNames = state.cardNames;

    state.script = buildScript(data.events);
    state.beatIndex = 0;
    state.shownObsIndex = null;
    state.lastRenderedObs = null;
    state.lastRenderedObsIndex = null;
    // Reset portraits so they rebuild for this game's players.
    $portraitLeft.dataset.portrait = "";
    $portraitRight.dataset.portrait = "";
    $scrubber.max = Math.max(0, state.script.length - 1);
    $scrubber.value = 0;
  }

  const NOTABLE = new Set([
    "CardsDrawn",
    "AttackersDeclared",
    "BlockersDeclared",
    "DamageDealt",
    "CreatureDestroyed",
    "LifeChanged",
  ]);

  function clsHasLines(cls) {
    return !!(cls.draws.length || cls.attackers.length || cls.blockers.length ||
      cls.damage.length || cls.deaths.length || cls.life.length);
  }

  // Engine sub-events linked to an action by action_id, scanning forward until
  // the next observation/action. Records the engine_event indices in outIdx.
  function collectOwnEngine(events, i, act, outIdx) {
    const out = [];
    for (let j = i + 1; j < events.length; j++) {
      const e = events[j];
      if (e.event === "observation" || e.event === "action") break;
      if (e.event === "engine_event" && e.action_id === act.action_id && e.player === act.player) {
        outIdx.push(j);
        out.push(...(e.events || []));
      }
    }
    return out;
  }

  /**
   * Flatten the event log into ordered, action-centric narration beats. Each
   * action bundles the player's monologue (inner thought) + table_talk (spoken
   * line) + the action it took + the engine result it produced (cards drawn,
   * attackers, combat damage, deaths). Notable engine events that aren't tied to
   * a shown action (e.g. the turn draw, or combat damage resolved on a pass) are
   * accumulated and attached to the next emitted beat — so a "(reaction: draw)"
   * shows the drawn card, and "(reaction: combat_resolution)" shows the outcome.
   */
  function buildScript(events) {
    const byId = state.playersById;
    const beats = [];
    let lastObs = null;
    let pending = [];           // accumulated notable sub-events not yet shown
    const consumed = new Set(); // engine_event indices already attributed

    for (let i = 0; i < events.length; i++) {
      const e = events[i];
      if (e.event === "observation") { lastObs = i; continue; }

      if (e.event === "engine_event") {
        if (!consumed.has(i)) {
          for (const ev of e.events || []) if (NOTABLE.has(ev.type)) pending.push(ev);
        }
        continue;
      }

      if (e.event === "commentary") {
        if (lastObs == null) continue;
        beats.push({ kind: "narrator", speaker: null, obsIndex: lastObs, turn: e.turn,
          stageLines: [], segments: [{ style: "narrator", text: e.text || "" }] });
        pending = [];
        continue;
      }

      if (e.event === "game_over") {
        beats.push({ kind: "result", speaker: null, obsIndex: lastObs, turn: e.turn,
          stageLines: [], segments: [{ style: "result", text: `Winner: ${e.winner || "draw"} · ${e.reason}` }] });
        pending = [];
        continue;
      }

      if (e.event !== "action" || lastObs == null) continue;
      if ((e.reasoning || "").startsWith("[autopass]")) continue;

      const rt = e.reaction_trigger;
      const desc = e.description || "";
      const thought = (e.monologues || []).map((s) => String(s).trim()).filter(Boolean).join("\n\n");
      const speech = (e.table_talk || []).map((s) => String(s).trim()).filter(Boolean).join("\n\n");
      const hasNarr = !!(thought || speech);
      const isPass = !rt && /^(pass|skip)\b/i.test(desc);
      // Structural decision prompts ("Declare attackers", "CHOOSE_BLOCKERS: …")
      // are distinct from the actual "Attack with X" / "Block X with Y" actions
      // that carry the engine result — drop the empty prompt beats.
      const isStructural = !rt && /^(declare attackers|declare blockers|choose_attackers|choose_blockers|no blocks)\b/i.test(desc);

      // A reaction carries no engine events of its own; real actions may.
      let own = [];
      const ownIdx = [];
      if (!rt) own = collectOwnEngine(events, i, e, ownIdx).filter((ev) => NOTABLE.has(ev.type));

      // Did the actual attacker/blocker set get declared on THIS action?
      const hasRealDecl = own.some((ev) =>
        (ev.type === "AttackersDeclared" && (ev.cardNames || []).length) ||
        (ev.type === "BlockersDeclared" && (ev.cardNames || []).length >= 2));

      const priorCls = D.classifyEngineEvents(pending, byId);
      const ownCls = D.classifyEngineEvents(own, byId);
      const hasResult = clsHasLines(priorCls) || clsHasLines(ownCls);

      let emit;
      if (rt) emit = hasNarr || hasResult; // skip empty reactions (e.g. no-block combat)
      else if (isStructural) emit = hasNarr || hasRealDecl; // drop empty "Declare X" prompts
      else if (isPass) emit = hasNarr; // bare passes never show; their results carry forward
      else emit = true;
      if (!emit) continue; // leave pending to carry to the next meaningful beat

      for (const idx of ownIdx) consumed.add(idx);
      pending = [];

      const beat = composeActionBeat(e, { rt, desc, isPass, isStructural, thought, speech, priorCls, ownCls, obsIndex: lastObs });
      if (beat.stageLines.length || beat.segments.length) beats.push(beat); // never push a blank beat
    }
    return beats;
  }

  // Build the display structure for one action beat: ordered stage-direction
  // lines (what happened, oldest first) plus typed dialogue segments. "Prior"
  // results (the turn draw, combat resolved on a pass) precede the action; the
  // action's own result follows it.
  function composeActionBeat(e, { rt, desc, isPass, isStructural, thought, speech, priorCls, ownCls, obsIndex }) {
    const stageLines = [];

    // 1) What happened before this action (e.g. the card just drawn this turn).
    if (priorCls.draws.length) stageLines.push(priorCls.draws.join(" · "));
    const priorCombat = [
      ...priorCls.attackers, ...priorCls.blockers,
      ...priorCls.damage, ...priorCls.deaths, ...priorCls.life,
    ];
    if (priorCombat.length) stageLines.push(priorCombat.join(" · "));

    // 2) The action itself. Reaction/structural prompts have no useful label of
    // their own — the engine result (draw / "attacks with …") speaks for them.
    const showDesc = (rt || isStructural) ? "" : desc;
    let primary = "";
    if (rt === "draw") primary = ""; // the draw is already the prior line above
    else if (ownCls.attackers.length) primary = ownCls.attackers.join("; ");
    else if (showDesc && !isPass) primary = showDesc;
    if (primary) stageLines.push(primary);

    // 3) The action's own result (a removal kill, combat it caused, …).
    const isBlock = /^block\b/i.test(desc);
    const ownRes = [];
    if (!isBlock) ownRes.push(...ownCls.blockers);
    ownRes.push(...ownCls.damage, ...ownCls.deaths, ...ownCls.life);
    if (ownRes.length) stageLines.push(ownRes.join(" · "));

    // Inner thought reads first, then the line spoken aloud.
    const segments = [];
    if (thought) segments.push({ style: "thought", text: thought });
    if (speech) segments.push({ style: "speech", text: speech });

    return { kind: "action", speaker: e.player, obsIndex, turn: e.turn, stageLines, segments };
  }

  // ------------------------------------------------------------- stage / mats

  function zonesByOwner(obs) {
    const map = new Map();
    for (const z of obs.zones || []) {
      if (!map.has(z.ownerId)) map.set(z.ownerId, {});
      map.get(z.ownerId)[z.zoneType] = z.cards || [];
    }
    return map;
  }

  function renderStage(obs, obsIndex) {
    if (!obs) return;
    // Highlight what changed since the last board we showed — but only when
    // moving forward, so rewinding doesn't flag "changes" in reverse.
    const forward = state.lastRenderedObsIndex == null || obsIndex > state.lastRenderedObsIndex;
    const diff = forward ? diffObs(state.lastRenderedObs, obs) : null;

    const byOwner = zonesByOwner(obs);
    const playerByName = {};
    for (const p of obs.players || []) playerByName[p.name] = p;
    renderMat($matA, playerByName[state.playerOrder[0]], byOwner, 0, obs.activePlayerId, diff);
    renderMat($matB, playerByName[state.playerOrder[1]], byOwner, 1, obs.activePlayerId, diff);
    renderTurnTracker(obs);

    state.lastRenderedObs = obs;
    state.lastRenderedObsIndex = obsIndex;
  }

  // What changed from the previously shown observation: which permanents are
  // newly on the battlefield, newly tapped, and each player's life delta.
  function diffObs(prev, cur) {
    if (!prev) return null;
    const prevEnt = new Set();
    const prevTapped = new Set();
    for (const z of prev.zones || []) {
      if (z.zoneType !== "Battlefield") continue;
      for (const c of z.cards || []) {
        if (!c.entityId) continue;
        prevEnt.add(c.entityId);
        if (c.tapped) prevTapped.add(c.entityId);
      }
    }
    const prevLife = {};
    const prevGy = {};
    for (const p of prev.players || []) { prevLife[p.id] = p.lifeTotal; prevGy[p.id] = p.graveyardSize; }
    return { prevEnt, prevTapped, prevLife, prevGy };
  }

  // Argentum-style turn progress: the five phases with the current one lit, plus
  // the turn number, active player, and the precise step.
  const PHASES = [
    { key: "BEGINNING", label: "Beginning" },
    { key: "PRECOMBAT_MAIN", label: "Main 1" },
    { key: "COMBAT", label: "Combat" },
    { key: "POSTCOMBAT_MAIN", label: "Main 2" },
    { key: "ENDING", label: "End" },
  ];

  function renderTurnTracker(obs) {
    $tracker.replaceChildren();
    const activeName = state.playersById[obs.activePlayerId] || "";
    const activeIdx = state.playerOrder.indexOf(activeName);

    const head = document.createElement("div");
    head.className = "tracker-head";
    const turn = document.createElement("span");
    turn.className = "tracker-turn";
    turn.textContent = `Turn ${obs.turnNumber}`;
    const who = document.createElement("span");
    who.className = "tracker-active";
    who.textContent = activeName;
    if (activeIdx >= 0) who.style.color = playerColor(activeIdx);
    head.appendChild(turn);
    head.appendChild(who);
    if (obs.terminated) {
      const over = document.createElement("span");
      over.className = "tracker-over";
      over.textContent = "GAME OVER";
      head.appendChild(over);
    }
    $tracker.appendChild(head);

    const rail = document.createElement("div");
    rail.className = "tracker-rail";
    const curIdx = PHASES.findIndex((p) => p.key === obs.phase);
    for (let i = 0; i < PHASES.length; i++) {
      const pip = document.createElement("div");
      pip.className = "tracker-phase";
      if (i === curIdx) pip.classList.add("current");
      else if (i < curIdx) pip.classList.add("done");
      pip.textContent = PHASES[i].label;
      rail.appendChild(pip);
    }
    $tracker.appendChild(rail);

    const step = document.createElement("div");
    step.className = "tracker-step";
    step.textContent = fmtStep(obs.step);
    $tracker.appendChild(step);
  }

  function fmtStep(step) {
    return String(step || "").replace(/_/g, " ").toLowerCase();
  }

  function renderMat(matEl, player, byOwner, idx, activeId, diff) {
    matEl.replaceChildren();
    matEl.className = `playmat playmat-${idx === 0 ? "a" : "b"}`;
    if (!player) return;
    if (player.id === activeId) matEl.classList.add("active");
    const zones = byOwner.get(player.id) || {};

    matEl.appendChild(matHeadEl(player, zones, idx, diff));

    const field = document.createElement("div");
    field.className = "mat-field";
    const bf = zones.Battlefield || [];
    field.appendChild(fieldRow("creatures", bf.filter((c) => hasCardType(c, "CREATURE")), diff));
    field.appendChild(fieldRow("lands", bf.filter((c) => hasCardType(c, "LAND")), diff));
    const other = bf.filter((c) => !hasCardType(c, "CREATURE") && !hasCardType(c, "LAND"));
    if (other.length) field.appendChild(fieldRow("other", other, diff));
    matEl.appendChild(field);

    matEl.appendChild(handRow(player, zones));
  }

  // A battlefield card with change highlights: newly-entered glow, newly-tapped
  // flag, and a marked-damage badge.
  function matCardEl(c, diff) {
    const el = cardEl(c);
    if (diff && c.entityId) {
      if (!diff.prevEnt.has(c.entityId)) el.classList.add("just-entered");
      else if (c.tapped && !diff.prevTapped.has(c.entityId)) el.classList.add("just-tapped");
    }
    if (c.damageMarked > 0) {
      const dmg = document.createElement("span");
      dmg.className = "card-damage";
      dmg.textContent = `-${c.damageMarked}`;
      el.appendChild(dmg);
    }
    return el;
  }

  function matHeadEl(player, zones, idx, diff) {
    const head = document.createElement("div");
    head.className = "mat-head";

    const name = document.createElement("div");
    name.className = "mat-name";
    name.textContent = player.name;
    name.style.color = playerColor(idx);
    head.appendChild(name);

    const life = document.createElement("div");
    life.className = "mat-life";
    life.textContent = `${player.lifeTotal}`;
    const heart = document.createElement("span");
    heart.className = "mat-life-heart";
    heart.textContent = "♥";
    life.appendChild(heart);
    const prevLife = diff && diff.prevLife[player.id];
    if (prevLife != null && prevLife !== player.lifeTotal) {
      const delta = player.lifeTotal - prevLife;
      const d = document.createElement("span");
      d.className = `mat-life-delta ${delta < 0 ? "down" : "up"}`;
      d.textContent = `${delta > 0 ? "+" : ""}${delta}`;
      life.appendChild(d);
    }
    head.appendChild(life);

    head.appendChild(manaPoolEl(player.manaPool));
    head.appendChild(zoneChipsEl(player, zones, diff));
    return head;
  }

  function manaPoolEl(pool) {
    const el = document.createElement("div");
    el.className = "mat-mana";
    for (const [color, amount] of Object.entries(pool || {})) {
      if (!amount) continue;
      const pip = document.createElement("span");
      pip.className = "mana-pip";
      pip.style.background = MANA_COLORS[color] || "#888";
      pip.textContent = amount > 1 ? String(amount) : "";
      pip.title = `${amount} ${color}`;
      el.appendChild(pip);
    }
    return el;
  }

  function zoneChipsEl(player, zones, diff) {
    const wrap = document.createElement("div");
    wrap.className = "mat-zones";
    wrap.appendChild(zoneChip(`hand ${player.handSize}`, false));
    wrap.appendChild(zoneChip(`lib ${player.librarySize}`, true, player, zones));
    const gyGrew = diff && diff.prevGy[player.id] != null && player.graveyardSize > diff.prevGy[player.id];
    wrap.appendChild(zoneChip(`gy ${player.graveyardSize}`, true, player, zones, gyGrew));
    if (player.exileSize) wrap.appendChild(zoneChip(`exile ${player.exileSize}`, true, player, zones));
    return wrap;
  }

  function zoneChip(label, clickable, player, zones, flash) {
    const el = document.createElement(clickable ? "button" : "span");
    el.className = "mat-zone-chip" + (clickable ? " mat-zone-btn" : "") + (flash ? " just-changed" : "");
    el.textContent = label;
    if (clickable) {
      el.type = "button";
      el.title = "view library & decklist";
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        window.openDeckModal?.(player, zones);
      });
    }
    return el;
  }

  function fieldRow(label, cards, diff) {
    const row = document.createElement("div");
    row.className = `mat-row mat-row-${label}`;
    const lbl = document.createElement("div");
    lbl.className = "mat-row-label";
    lbl.textContent = `${label} (${cards.length})`;
    row.appendChild(lbl);
    const list = document.createElement("div");
    list.className = "mat-cards";
    if (!cards.length) {
      const empty = document.createElement("span");
      empty.className = "empty";
      empty.textContent = "—";
      list.appendChild(empty);
    } else {
      for (const c of cards) list.appendChild(matCardEl(c, diff));
    }
    row.appendChild(list);
    return row;
  }

  function handRow(player, zones) {
    const row = document.createElement("div");
    row.className = "mat-row mat-row-hand";
    const lbl = document.createElement("div");
    lbl.className = "mat-row-label";
    lbl.textContent = `hand (${player.handSize})`;
    row.appendChild(lbl);
    const list = document.createElement("div");
    list.className = "mat-cards mat-hand-cards";
    const cards = zones.Hand || [];
    if (cards.length) {
      for (const c of cards) list.appendChild(cardEl(c));
    } else if (player.handSize > 0) {
      for (let i = 0; i < player.handSize; i++) list.appendChild(cardBackEl());
    } else {
      const empty = document.createElement("span");
      empty.className = "empty";
      empty.textContent = "—";
      list.appendChild(empty);
    }
    row.appendChild(list);
    return row;
  }

  function cardBackEl() {
    const el = cardEl({ faceDown: true, name: "" });
    el.classList.add("card-back");
    el.querySelector(".card-name")?.remove();
    return el;
  }

  // ------------------------------------------------------------- narration

  function showBeat(n, opts = {}) {
    if (!state.script.length) {
      $text.textContent = "(no narration in this game)";
      return;
    }
    stopAutoplay();
    n = Math.max(0, Math.min(n, state.script.length - 1));
    state.beatIndex = n;
    const beat = state.script[n];

    if (beat.obsIndex !== state.shownObsIndex) {
      const obs = state.events[beat.obsIndex]?.obs;
      renderStage(obs, beat.obsIndex);
      updateStateLabel(obs);
      state.shownObsIndex = beat.obsIndex;
    }

    renderNarration(beat, !!opts.type);

    $scrubber.value = n;
    $beatLabel.textContent = `${n + 1} / ${state.script.length}`;
    updateUrl(n);
  }

  function renderNarration(beat, doType) {
    clearTyping();
    $narration.className = `kind-${beat.kind}`;
    setSpeakerPlate(beat);
    setPortraits(beat);

    // Stage directions (what happened / what they did) render immediately.
    $narrStage.replaceChildren();
    const stageLines = beat.stageLines || [];
    for (const line of stageLines) {
      const el = document.createElement("div");
      el.className = "stage-line";
      window.appendRichText(el, line);
      $narrStage.appendChild(el);
    }
    $narrStage.style.display = stageLines.length ? "" : "none";

    // Dialogue segments (spoken + thought) type out in sequence.
    $text.replaceChildren();
    const segs = beat.segments || [];
    state.curSegs = segs;
    state.curSegEls = segs.map((s) => {
      const el = document.createElement("div");
      el.className = `dialogue-line line-${s.style}`;
      $text.appendChild(el);
      return el;
    });
    if (doType && segs.length) {
      typeSegments(0, 0);
    } else {
      for (let k = 0; k < segs.length; k++) window.appendRichText(state.curSegEls[k], segs[k].text);
      state.typing = false;
      showAdvanceIndicator();
      scheduleAutoplay();
    }
  }

  function typeSegments(segIdx, charIdx) {
    state.typing = true;
    hideAdvanceIndicator();
    const segs = state.curSegs;
    if (segIdx >= segs.length) { finishTyping(); return; }
    const full = segs[segIdx].text || "";
    if (charIdx >= full.length) { typeSegments(segIdx + 1, 0); return; }
    state.curSegEls[segIdx].textContent = full.slice(0, charIdx + 1);
    state.typeTimer = setTimeout(() => typeSegments(segIdx, charIdx + 1), charDelay(full[charIdx]));
  }

  function charDelay(ch) {
    if (".!?".includes(ch)) return 200;
    if (",;:—".includes(ch)) return 85;
    return 15;
  }

  function finishTyping() {
    if (state.typeTimer) clearTimeout(state.typeTimer);
    state.typeTimer = null;
    state.typing = false;
    const segs = state.curSegs || [];
    for (let k = 0; k < segs.length; k++) {
      const el = state.curSegEls[k];
      el.replaceChildren();
      window.appendRichText(el, segs[k].text);
    }
    showAdvanceIndicator();
    scheduleAutoplay();
  }

  function clearTyping() {
    if (state.typeTimer) clearTimeout(state.typeTimer);
    state.typeTimer = null;
    state.typing = false;
  }

  function setSpeakerPlate(beat) {
    $plate.replaceChildren();
    let name = "";
    if (beat.kind === "action") name = beat.speaker || "";
    else if (beat.kind === "narrator") name = "Commentator";
    else if (beat.kind === "result") name = "Game Over";
    $plate.style.display = "";
    if (name) {
      const n = document.createElement("span");
      n.className = "speaker-name";
      n.textContent = name;
      if (beat.speaker) n.style.color = playerColor(state.playerOrder.indexOf(beat.speaker));
      $plate.appendChild(n);
    }
    // Mirror the top tracker: which turn/phase this line is narrating.
    const obs = state.events[beat.obsIndex]?.obs;
    if (obs) {
      const ctx = document.createElement("span");
      ctx.className = "speaker-context";
      ctx.textContent = `Turn ${obs.turnNumber} · ${phaseLabel(obs.phase)} · ${fmtStep(obs.step)}`;
      $plate.appendChild(ctx);
    }
  }

  function phaseLabel(phaseKey) {
    const p = PHASES.find((x) => x.key === phaseKey);
    return p ? p.label : fmtStep(phaseKey);
  }

  function setPortraits(beat) {
    setPortrait($portraitLeft, state.playerOrder[0], 0);
    setPortrait($portraitRight, state.playerOrder[1], 1);
    $portraitLeft.classList.toggle("speaking", beat.speaker && beat.speaker === state.playerOrder[0]);
    $portraitRight.classList.toggle("speaking", beat.speaker && beat.speaker === state.playerOrder[1]);
  }

  function setPortrait(el, name, idx) {
    if (el.dataset.portrait === (name || "")) return; // already built
    el.dataset.portrait = name || "";
    el.replaceChildren();
    el.classList.remove("has-portrait");
    if (!name) { el.classList.add("empty"); return; }
    el.classList.remove("empty");
    el.style.setProperty("--portrait-color", playerColor(idx));

    const fb = document.createElement("div");
    fb.className = "portrait-fallback";
    const initial = document.createElement("span");
    initial.className = "portrait-initial";
    initial.textContent = name[0].toUpperCase();
    const nm = document.createElement("span");
    nm.className = "portrait-name";
    nm.textContent = name;
    fb.appendChild(initial);
    fb.appendChild(nm);
    el.appendChild(fb);

    const img = new Image();
    img.className = "portrait-img";
    img.alt = name;
    img.addEventListener("error", () => img.remove());
    img.addEventListener("load", () => el.classList.add("has-portrait"));
    img.src = `static/portraits/${encodeURIComponent(name)}.png`;
    el.appendChild(img);
  }

  function showAdvanceIndicator() {
    const atEnd = state.beatIndex >= state.script.length - 1;
    $advance.style.visibility = atEnd ? "hidden" : "visible";
  }

  function hideAdvanceIndicator() {
    $advance.style.visibility = "hidden";
  }

  function updateStateLabel(obs) {
    // Turn / phase now live in the in-stage tracker; keep the top bar uncluttered.
    if (state.gameId) $stateLabel.textContent = state.gameId;
  }

  function updateUrl(n) {
    const params = new URLSearchParams(window.location.search);
    if (state.gameId) params.set("game", state.gameId);
    params.set("beat", String(n));
    window.history.replaceState(null, "", `${window.location.pathname}?${params.toString()}`);
  }

  // ------------------------------------------------------------- navigation

  function advance() {
    if (state.typing) { finishTyping(); return; }
    if (state.beatIndex >= state.script.length - 1) return;
    showBeat(state.beatIndex + 1, { type: true });
  }

  function prevBeat() {
    showBeat(state.beatIndex - 1, { type: false });
  }

  // ------------------------------------------------------------- autoplay

  function toggleAutoplay() {
    state.autoplay = !state.autoplay;
    $autoplay.classList.toggle("on", state.autoplay);
    $autoplay.textContent = state.autoplay ? "⏸ auto" : "▶ auto";
    if (state.autoplay && !state.typing) scheduleAutoplay();
    else stopAutoplay();
  }

  function disableAutoplay() {
    if (!state.autoplay) return;
    state.autoplay = false;
    $autoplay.classList.remove("on");
    $autoplay.textContent = "▶ auto";
    stopAutoplay();
  }

  function beatTextLength(beat) {
    if (!beat) return 0;
    let n = 0;
    for (const s of beat.stageLines || []) n += s.length;
    for (const s of beat.segments || []) n += (s.text || "").length;
    return n;
  }

  function scheduleAutoplay() {
    if (!state.autoplay) return;
    if (state.autoTimer) clearTimeout(state.autoTimer);
    if (state.beatIndex >= state.script.length - 1) { return; }
    const delay = Math.min(6000, 1100 + beatTextLength(state.script[state.beatIndex]) * 22);
    state.autoTimer = setTimeout(() => {
      if (!state.autoplay) return;
      showBeat(state.beatIndex + 1, { type: true });
    }, delay);
  }

  function stopAutoplay() {
    if (state.autoTimer) clearTimeout(state.autoTimer);
    state.autoTimer = null;
  }
})();

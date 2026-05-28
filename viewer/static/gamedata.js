/* The Stack — shared data layer for the replay viewers.
 *
 * Both viewers (the debug SPA at index.html / app.js and the visual-novel
 * theater at theater.html / theater.js) load the same game.jsonl, oracle, and
 * personas, and share the same card-name rich-text linkification and the
 * deck/library modal. To stay page-agnostic, the rich-text and modal helpers
 * read the canonical card names / decks / oracle from window globals that each
 * page keeps populated:
 *
 *   window.thestackOracle     {cardName: {type_line, mana_cost, oracle_text, ...}}
 *   window.thestackDecks      {playerName: {total, cards:[{name,count,sample}]}}
 *   window.thestackCardNames  [canonical names, longest-first, for hover linking]
 *
 * board.js (loaded first) provides window.attachCardHover, used here.
 */
(function () {
  "use strict";

  // ------------------------------------------------------------- fetch I/O

  async function fetchJsonCandidates(urls) {
    const errors = [];
    for (const url of urls) {
      try {
        const r = await fetch(url);
        if (!r.ok) {
          errors.push(`${url}: ${r.status}`);
          continue;
        }
        return await r.json();
      } catch (e) {
        errors.push(`${url}: ${e.message || e}`);
      }
    }
    throw new Error(`unable to load JSON (${errors.join("; ")})`);
  }

  async function fetchTextCandidates(urls) {
    const errors = [];
    for (const url of urls) {
      try {
        const r = await fetch(url);
        if (!r.ok) {
          errors.push(`${url}: ${r.status}`);
          continue;
        }
        return await r.text();
      } catch (e) {
        errors.push(`${url}: ${e.message || e}`);
      }
    }
    throw new Error(`unable to load text (${errors.join("; ")})`);
  }

  function parseJsonLines(text) {
    const events = [];
    for (const line of String(text || "").split(/\r?\n/)) {
      const trimmed = line.trim();
      if (trimmed) events.push(JSON.parse(trimmed));
    }
    return events;
  }

  // Oracle map with both original and lowercased keys for forgiving lookups.
  async function loadOracle() {
    const raw = await fetchJsonCandidates(["data/oracle.json", "/api/oracle"]);
    const map = {};
    for (const [name, card] of Object.entries(raw)) {
      map[name] = card;
      map[name.toLowerCase()] = card;
    }
    return map;
  }

  async function fetchGameList() {
    return fetchJsonCandidates(["data/games.json", "/api/games"]);
  }

  async function fetchGameEvents(gameId) {
    const safeGameId = encodeURIComponent(gameId);
    try {
      const text = await fetchTextCandidates([`data/games/${safeGameId}/game.jsonl`]);
      return parseJsonLines(text);
    } catch (_textError) {
      try {
        const j = await fetchJsonCandidates([`data/games/${safeGameId}/game.json`]);
        return j.events || [];
      } catch (_jsonError) {
        const j = await fetchJsonCandidates([`/api/games/${safeGameId}`]);
        return j.events || [];
      }
    }
  }

  async function fetchPersonas(gameId) {
    const safeGameId = encodeURIComponent(gameId);
    try {
      return await fetchJsonCandidates([
        `data/games/${safeGameId}/personas.json`,
        `/api/games/${safeGameId}/personas`,
      ]);
    } catch (_e) {
      return {};
    }
  }

  // ------------------------------------------------------------- indexing

  function indexObservations(events) {
    const observations = [];
    const playersById = {};
    const playerOrder = [];
    for (let i = 0; i < events.length; i++) {
      if (events[i].event !== "observation") continue;
      observations.push(i);
      for (const p of events[i].obs?.players || []) {
        playersById[p.id] = p.name;
        if (!playerOrder.includes(p.name)) playerOrder.push(p.name);
      }
    }
    return { observations, playersById, playerOrder };
  }

  /**
   * Observation indices a scrubber should stop on. Skip observations whose
   * preceding action was an autopass — the "both bots passed through upkeep"
   * beats nobody wants to step through. Always keep first and last.
   */
  function scrubObservations(observations, events, showAll) {
    if (showAll) return [...observations];
    const keep = [];
    for (let k = 0; k < observations.length; k++) {
      const obsIdx = observations[k];
      if (k === 0 || k === observations.length - 1) {
        keep.push(obsIdx);
        continue;
      }
      let producedByAutopass = false;
      for (let j = obsIdx - 1; j >= 0; j--) {
        const ev = events[j];
        if (ev.event === "action") {
          producedByAutopass = (ev.reasoning || "").startsWith("[autopass]");
          break;
        }
        if (ev.event === "observation") break;
      }
      if (!producedByAutopass) keep.push(obsIdx);
    }
    return keep;
  }

  // ------------------------------------------------------- card name utils

  function cardDisplayName(card) {
    if (!card) return "";
    if (card.name) return card.name;
    return String(card.cardDefinitionId || "").split("#")[0];
  }

  function addCardName(names, name) {
    const clean = String(name || "").trim();
    if (clean) names.add(clean);
  }

  function addCardNames(names, cardNames) {
    for (const name of cardNames || []) addCardName(names, name);
  }

  function collectCardNamesFromObservation(names, obs) {
    if (!obs) return;
    for (const z of obs.zones || []) {
      for (const c of z.cards || []) addCardName(names, cardDisplayName(c));
    }
    for (const c of obs.stack || []) addCardName(names, cardDisplayName(c));
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

  // Dedupe case-insensitively (keep first spelling), sort longest-first so the
  // rich-text matcher prefers the most specific name.
  function sortCardNames(names) {
    const byLower = new Map();
    for (const name of names || []) {
      const clean = String(name || "").trim();
      if (!clean) continue;
      const key = clean.toLowerCase();
      if (!byLower.has(key)) byLower.set(key, clean);
    }
    return [...byLower.values()].sort((a, b) => b.length - a.length || a.localeCompare(b));
  }

  // --------------------------------------------------------------- decks

  function isLandName(name) {
    const oracle = window.thestackOracle || {};
    const card = oracle[name] || oracle[name.toLowerCase()];
    return /land/i.test(card?.type_line || "");
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

  function countCardsFromZones(zones) {
    const cards = [];
    for (const zoneCards of Object.values(zones || {})) cards.push(...(zoneCards || []));
    return countCards(cards);
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
    for (const [player, cards] of Object.entries(byPlayer)) decks[player] = countCards(cards);
    return decks;
  }

  // ------------------------------------------------------------- load game

  async function loadGame(gameId) {
    const [events, personas] = await Promise.all([
      fetchGameEvents(gameId),
      fetchPersonas(gameId),
    ]);
    const { observations, playersById, playerOrder } = indexObservations(events);
    return {
      gameId,
      events,
      observations,
      playersById,
      playerOrder,
      initialDecks: collectInitialDecks(events),
      cardNames: collectCardNamesFromEvents(events),
      personas,
    };
  }

  // ----------------------------------------------- engine event classifier

  function enginePlayerNames(ev, byId) {
    const out = [];
    for (const id of ev.playerIds || []) {
      const n = byId[id];
      if (n && !out.includes(n)) out.push(n);
    }
    return out;
  }

  function damageTarget(ev, byId) {
    for (const id of (ev.entityIds || []).slice(1)) {
      if (byId[id]) return byId[id];
    }
    if ((ev.cardNames || []).length > 1 && ev.cardNames[1] !== "Player") return ev.cardNames[1];
    return "a player";
  }

  /**
   * Group a list of raw engine sub-events into VN-friendly, present-tense
   * lines by category. Used by the theater to narrate draws and combat.
   */
  function classifyEngineEvents(subEvents, byId) {
    byId = byId || {};
    const r = { draws: [], attackers: [], blockers: [], damage: [], deaths: [], life: [] };
    for (const ev of subEvents || []) {
      const who = enginePlayerNames(ev, byId).join(" & ");
      const cards = (ev.cardNames || []).filter(Boolean);
      switch (ev.type) {
        case "CardsDrawn":
          r.draws.push(`${who || "A player"} draws ${cards.length ? cards.join(", ") : `${ev.amount || "a"} card(s)`}`);
          break;
        case "AttackersDeclared":
          if (cards.length) r.attackers.push(`${who || "Attacker"} attacks with ${cards.join(", ")}`);
          break;
        case "BlockersDeclared":
          if (cards.length >= 2) r.blockers.push(ev.text || `${cards[0]} blocks ${cards[1]}`);
          break;
        case "DamageDealt":
          r.damage.push(`${cards[0] || "A source"} deals ${ev.amount || "?"} to ${damageTarget(ev, byId)}`);
          break;
        case "CreatureDestroyed":
          r.deaths.push(`${cards[0] || "A creature"} dies`);
          break;
        case "LifeChanged": {
          const m = (ev.text || "").match(/(-?\d+)\s*->\s*(-?\d+)/);
          if (m) r.life.push(`${who || "A player"} ${m[1]} → ${m[2]}`);
          break;
        }
        default:
          break;
      }
    }
    for (const k of Object.keys(r)) r[k] = [...new Set(r[k])];
    return r;
  }

  // --------------------------------------------------- rich text (card links)

  function isCardNameBoundary(ch) {
    return !ch || !/[A-Za-z0-9]/.test(ch);
  }

  function matchingCardName(text, index) {
    for (const name of window.thestackCardNames || []) {
      if (text.slice(index, index + name.length).toLowerCase() !== name.toLowerCase()) continue;
      if (!isCardNameBoundary(text[index - 1]) || !isCardNameBoundary(text[index + name.length])) {
        continue;
      }
      return name;
    }
    return "";
  }

  function nextCardNameIndex(text, start) {
    for (let i = start; i < text.length; i++) {
      if (matchingCardName(text, i)) return i;
    }
    return text.length;
  }

  // Append text to el, wrapping any known card names in hoverable tokens.
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

  // --------------------------------------------------------- deck/library modal

  function appendCardToken(parent, name) {
    const token = document.createElement("span");
    token.className = "card-text-token";
    token.textContent = name || "?";
    if (typeof window.attachCardHover === "function") window.attachCardHover(token, { name });
    parent.appendChild(token);
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
    const oracle = window.thestackOracle || {};
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
      const card = oracle[row.name] || oracle[row.name.toLowerCase()];
      const type = card?.type_line || row.sample?.types?.join(" ");
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

  function openDeckModal(player, zones = {}) {
    const modal = document.getElementById("deck-modal");
    const body = document.getElementById("deck-modal-body");
    const title = document.getElementById("deck-modal-title");
    const meta = document.getElementById("deck-modal-meta");
    if (!modal || !body) return;

    const decks = window.thestackDecks || {};
    const deck = decks[player.name] || countCardsFromZones(zones);
    const library = zones.Library || [];

    if (title) title.textContent = `${player.name} — library and deck`;
    if (meta) {
      meta.textContent =
        `${library.length} cards currently in library · ${deck.total} cards in original deck`;
    }
    body.replaceChildren();

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

    body.appendChild(librarySection);
    body.appendChild(deckSection);
    modal.classList.remove("hidden");
  }

  function closeDeckModal() {
    const modal = document.getElementById("deck-modal");
    if (!modal || modal.classList.contains("hidden")) return;
    modal.classList.add("hidden");
    document.getElementById("deck-modal-body")?.replaceChildren();
  }

  // Wire close button / backdrop / escape. Idempotent (guards re-binding).
  function initDeckModal() {
    const modal = document.getElementById("deck-modal");
    if (!modal || modal.dataset.wired === "1") return;
    modal.dataset.wired = "1";
    document.getElementById("deck-modal-close")?.addEventListener("click", closeDeckModal);
    modal.addEventListener("click", (e) => {
      if (e.target.dataset.closeModal === "deck") closeDeckModal();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeDeckModal();
    });
  }

  function deckModalOpen() {
    const modal = document.getElementById("deck-modal");
    return !!modal && !modal.classList.contains("hidden");
  }

  // --------------------------------------------------------------- exports

  window.TheStackData = {
    fetchJsonCandidates,
    fetchTextCandidates,
    parseJsonLines,
    loadOracle,
    fetchGameList,
    loadGame,
    scrubObservations,
    sortCardNames,
    collectCardNamesFromEvents,
    collectInitialDecks,
    countCards,
    cardDisplayName,
    isLandName,
    classifyEngineEvents,
    initDeckModal,
    deckModalOpen,
  };
  window.appendRichText = appendRichText;
  window.openDeckModal = openDeckModal;
  window.closeDeckModal = closeDeckModal;
})();

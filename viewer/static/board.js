/* Minimal HTML board renderer for The Stack viewer.
 *
 * renderBoard(panelEl, obs) — replace panelEl's contents with a snapshot of
 * the given Argentum observation. Cards use Scryfall image fallbacks by name
 * with oracle-text hover details.
 */

const CARD_BACK_IMAGE_URL =
  "https://backs.scryfall.io/normal/2/2/222b7a3b-2321-4d4c-af19-19338b134971.jpg?1677416389";

let activeCardTooltip = null;
let lastTooltipMouseEvent = null;

function showCardTooltip(tip, event) {
  if (activeCardTooltip && activeCardTooltip !== tip) hideActiveCardTooltip();
  activeCardTooltip = tip;
  lastTooltipMouseEvent = event;
  if (!tip.isConnected) document.body.appendChild(tip);
  tip.classList.add("visible");
  positionCardTooltip(tip, event);
  requestAnimationFrame(() => positionCardTooltip(tip, event));
}

function moveCardTooltip(tip, event) {
  if (activeCardTooltip !== tip) return;
  lastTooltipMouseEvent = event;
  positionCardTooltip(tip, event);
}

function hideActiveCardTooltip() {
  if (!activeCardTooltip) return;
  activeCardTooltip.classList.remove("visible");
  activeCardTooltip.remove();
  activeCardTooltip = null;
  lastTooltipMouseEvent = null;
}

function positionCardTooltip(tip, event) {
  if (!event) return;
  const gap = 18;
  const pad = 12;
  const rect = tip.getBoundingClientRect();
  const width = rect.width || 310;
  const height = rect.height || 440;
  let left = event.clientX + gap;
  if (left + width + pad > window.innerWidth) left = event.clientX - width - gap;
  left = Math.max(pad, Math.min(left, window.innerWidth - width - pad));

  let top = event.clientY - height / 2;
  top = Math.max(pad, Math.min(top, window.innerHeight - height - pad));

  tip.style.left = `${Math.round(left)}px`;
  tip.style.top = `${Math.round(top)}px`;
}

function fmtZoneLabel(zoneType) {
  return zoneType.replace(/_/g, " ").toLowerCase();
}

function cardEl(c) {
  const el = document.createElement("div");
  el.className = "card";
  el.dataset.cardName = c.name || "?";
  if (c.tapped) el.classList.add("tapped");
  if (c.summoningSick) el.classList.add("sick");

  const imageUrl = getCardImageUrl(c, "small");
  if (imageUrl) {
    el.classList.add("has-art");
    const img = document.createElement("img");
    img.className = "card-art";
    img.src = imageUrl;
    img.alt = c.name || "card";
    img.loading = "lazy";
    img.addEventListener("error", () => {
      img.remove();
      el.classList.remove("has-art");
      el.classList.add("image-missing");
    });
    el.appendChild(img);
  }

  const name = document.createElement("span");
  name.className = "card-name";
  name.textContent = c.name || "?";
  el.appendChild(name);

  if (c.power != null) {
    const pt = document.createElement("span");
    pt.className = "pt";
    pt.textContent = ` ${c.power}/${c.toughness}`;
    el.appendChild(pt);
  }

  attachCardHover(el, c);

  return el;
}

function cardNameTokenEl(c) {
  const el = document.createElement("span");
  el.className = "card-text-token";
  el.textContent = c.name || "?";
  attachCardHover(el, c);
  return el;
}

function attachCardHover(el, c) {
  let tip = null;
  el.addEventListener("mouseenter", (event) => {
    tip = tip || buildCardTooltip(c);
    if (tip) showCardTooltip(tip, event);
  });
  el.addEventListener("mousemove", (event) => {
    if (tip) moveCardTooltip(tip, event);
  });
  el.addEventListener("mouseleave", hideActiveCardTooltip);
}

window.attachCardHover = attachCardHover;

function buildCardTooltip(c) {
  // Prefer scryfall data (loaded into window.thestackOracle by app.js init)
  // when the obs's own oracleText is empty; the gym only populates it for
  // lands and a few other rule-based cards.
  const scry = lookupOracle(c);

  const cost = c.manaCost || scry?.mana_cost || "";
  const typeLine = scry?.type_line || (c.types?.length ? c.types.join(", ") : "");
  const oracle = c.oracleText || scry?.oracle_text || "";
  const imageUrl = getCardImageUrl(c, "normal");
  if (!imageUrl && !cost && !typeLine && !oracle && c.power == null) return null;

  const tip = document.createElement("div");
  tip.className = "card-tooltip";
  if (imageUrl) {
    const img = document.createElement("img");
    img.className = "card-tooltip-image";
    img.src = imageUrl;
    img.alt = c.name || "card";
    img.loading = "lazy";
    img.addEventListener("error", () => img.remove());
    img.addEventListener("load", () => {
      if (activeCardTooltip === tip) positionCardTooltip(tip, lastTooltipMouseEvent);
    });
    tip.appendChild(img);
  }

  const header = document.createElement("div");
  header.className = "card-tooltip-head";
  let pt = "";
  if (c.power != null) pt = `  ${c.power}/${c.toughness}`;
  else if (scry?.power != null) pt = `  ${scry.power}/${scry.toughness}`;
  header.textContent = `${c.name}${pt}  ${cost}`.trim();
  tip.appendChild(header);
  if (typeLine) {
    const t = document.createElement("div");
    t.className = "card-tooltip-type";
    t.textContent = typeLine;
    tip.appendChild(t);
  }
  if (oracle) {
    const o = document.createElement("div");
    o.className = "card-tooltip-oracle";
    o.textContent = oracle;
    tip.appendChild(o);
  }
  return tip;
}

function getCardImageUrl(c, version = "normal") {
  if (c.faceDown) return CARD_BACK_IMAGE_URL;
  const scry = lookupOracle(c);
  const direct =
    c.imageUri ||
    c.image_uri ||
    scry?.image_uri ||
    scry?.imageUri ||
    scry?.image_uris?.[version] ||
    scry?.imageUris?.[version];
  if (direct) return direct;
  return getScryfallFallbackUrl(c.name, version);
}

function getScryfallFallbackUrl(cardName, version = "normal") {
  if (!cardName) return "";
  // Match Argentum's fallback: token names often have a local " Token" suffix
  // that Scryfall's exact lookup does not use.
  const scryfallName = cardName.endsWith(" Token") ? cardName.slice(0, -6) : cardName;
  return `https://api.scryfall.com/cards/named?exact=${encodeURIComponent(scryfallName)}&format=image&version=${version}`;
}

function lookupOracle(c) {
  const oracleMap = window.thestackOracle || {};
  const name = (c.name || "").trim();
  const definitionName = (c.cardDefinitionId || "").split("#")[0].trim();
  return (
    oracleMap[name] ||
    oracleMap[name.toLowerCase()] ||
    oracleMap[definitionName] ||
    oracleMap[definitionName.toLowerCase()] ||
    null
  );
}

function hasCardType(c, type) {
  const wanted = type.toUpperCase();
  return (c.types || []).some((t) => String(t).toUpperCase() === wanted);
}

function zoneEl(label, cards) {
  const el = document.createElement("div");
  el.className = "zone";
  const lbl = document.createElement("div");
  lbl.className = "zone-label";
  lbl.textContent = `${label} (${cards.length})`;
  el.appendChild(lbl);
  const list = document.createElement("div");
  list.className = "cards";
  if (cards.length === 0) {
    const empty = document.createElement("span");
    empty.className = "empty";
    empty.textContent = "—";
    list.appendChild(empty);
  } else {
    for (const c of cards) list.appendChild(cardEl(c));
  }
  el.appendChild(list);
  return el;
}

function battlefieldZoneEl(cards, isTopPlayer = false) {
  const creatures = cards.filter((c) => hasCardType(c, "CREATURE"));
  const lands = cards.filter((c) => hasCardType(c, "LAND"));
  const other = cards.filter((c) => !hasCardType(c, "CREATURE") && !hasCardType(c, "LAND"));

  const el = document.createElement("div");
  el.className = "zone battlefield-zone";
  const lbl = document.createElement("div");
  lbl.className = "zone-label";
  lbl.textContent = `battlefield (${cards.length})`;
  el.appendChild(lbl);

  if (isTopPlayer) {
    if (other.length) el.appendChild(battlefieldRowEl("other", other, ""));
    el.appendChild(battlefieldRowEl("lands", lands, "no lands"));
    el.appendChild(battlefieldRowEl("creatures", creatures, "no creatures"));
  } else {
    el.appendChild(battlefieldRowEl("creatures", creatures, "no creatures"));
    el.appendChild(battlefieldRowEl("lands", lands, "no lands"));
    if (other.length) el.appendChild(battlefieldRowEl("other", other, ""));
  }
  return el;
}

function battlefieldRowEl(label, cards, emptyText) {
  const row = document.createElement("div");
  row.className = `battlefield-row battlefield-${label}`;

  const lbl = document.createElement("div");
  lbl.className = "battlefield-row-label";
  lbl.textContent = `${label} (${cards.length})`;
  row.appendChild(lbl);

  const list = document.createElement("div");
  list.className = "cards";
  if (cards.length === 0 && emptyText) {
    const empty = document.createElement("span");
    empty.className = "empty";
    empty.textContent = emptyText;
    list.appendChild(empty);
  } else {
    for (const c of cards) list.appendChild(cardEl(c));
  }
  row.appendChild(list);
  return row;
}

function playerBoardEl(player, zonesByOwner, isActing, isTopPlayer = false) {
  const wrap = document.createElement("div");
  wrap.className = "player-board" + (isActing ? " active" : "");

  const zones = zonesByOwner.get(player.id) || {};
  const layout = document.createElement("div");
  layout.className = "player-zones-layout";
  const main = document.createElement("div");
  main.className = "player-main-zones";
  const side = document.createElement("div");
  side.className = "player-side-zones";

  side.appendChild(playerSummaryEl(player, zones));
  side.appendChild(sideZoneEl("graveyard", zones.Graveyard || []));
  if ((zones.Exile || []).length > 0) side.appendChild(sideZoneEl("exile", zones.Exile || []));
  if (isTopPlayer) {
    main.appendChild(zoneEl("hand", zones.Hand || []));
    main.appendChild(battlefieldZoneEl(zones.Battlefield || [], true));
  } else {
    main.appendChild(battlefieldZoneEl(zones.Battlefield || [], false));
    main.appendChild(zoneEl("hand", zones.Hand || []));
  }

  layout.appendChild(main);
  layout.appendChild(side);
  wrap.appendChild(layout);
  return wrap;
}

function playerSummaryEl(player, zones = {}) {
  const el = document.createElement("div");
  el.className = "player-summary";
  const name = document.createElement("div");
  name.className = "player-summary-name";
  name.textContent = player.name;
  const life = document.createElement("div");
  life.className = "player-summary-life";
  life.textContent = `${player.lifeTotal}♥`;
  const meta = document.createElement("div");
  meta.className = "player-summary-meta";
  meta.textContent = `${player.handSize} hand · ${player.librarySize} lib`;
  const deckButton = document.createElement("button");
  deckButton.className = "player-deck-button";
  deckButton.type = "button";
  deckButton.textContent = "view deck";
  deckButton.addEventListener("click", (event) => {
    event.stopPropagation();
    window.openDeckModal?.(player, zones);
  });
  el.appendChild(name);
  el.appendChild(life);
  el.appendChild(meta);
  el.appendChild(deckButton);
  return el;
}

function sideZoneEl(label, cards) {
  const el = document.createElement("div");
  el.className = "zone side-zone";
  const lbl = document.createElement("div");
  lbl.className = "zone-label";
  lbl.textContent = `${label} (${cards.length})`;
  el.appendChild(lbl);
  const list = document.createElement("div");
  list.className = "side-zone-cards";
  if (cards.length === 0) {
    const empty = document.createElement("span");
    empty.className = "empty";
    empty.textContent = "—";
    list.appendChild(empty);
  } else {
    for (const c of cards) list.appendChild(cardNameTokenEl(c));
  }
  el.appendChild(list);
  return el;
}

// eslint-disable-next-line no-unused-vars
function renderBoard(panel, obs) {
  hideActiveCardTooltip();
  panel.replaceChildren();
  if (!obs) {
    panel.textContent = "No observation yet.";
    return;
  }

  const zonesByOwner = new Map();
  for (const z of obs.zones || []) {
    if (!zonesByOwner.has(z.ownerId)) zonesByOwner.set(z.ownerId, {});
    zonesByOwner.get(z.ownerId)[z.zoneType] = z.cards || [];
  }

  const acting = obs.agentToAct;
  for (const [idx, p] of (obs.players || []).entries()) {
    panel.appendChild(playerBoardEl(p, zonesByOwner, p.id === acting, idx === 0));
  }

  const stack = obs.stack || [];
  if (stack.length) {
    const s = document.createElement("div");
    s.className = "stack-zone";
    s.innerHTML = "<div class='zone-label'>stack (top last)</div>";
    const list = document.createElement("div");
    list.className = "cards";
    for (const item of stack) list.appendChild(cardEl(item));
    s.appendChild(list);
    panel.appendChild(s);
  }
}

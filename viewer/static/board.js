/* Minimal HTML board renderer for The Stack viewer.
 *
 * renderBoard(panelEl, obs) — replace panelEl's contents with a snapshot of
 * the given Argentum observation. No interactivity beyond hover tooltips
 * with oracle text.
 */

function fmtZoneLabel(zoneType) {
  return zoneType.replace(/_/g, " ").toLowerCase();
}

function cardEl(c) {
  const el = document.createElement("div");
  el.className = "card";
  if (c.tapped) el.classList.add("tapped");
  if (c.summoningSick) el.classList.add("sick");

  const name = document.createElement("span");
  name.textContent = c.name || "?";
  el.appendChild(name);

  if (c.power != null) {
    const pt = document.createElement("span");
    pt.className = "pt";
    pt.textContent = ` ${c.power}/${c.toughness}`;
    el.appendChild(pt);
  }

  const tip = buildCardTooltip(c);
  if (tip) el.appendChild(tip);

  return el;
}

function buildCardTooltip(c) {
  // Prefer scryfall data (loaded into window.thestackOracle by app.js init)
  // when the obs's own oracleText is empty; the gym only populates it for
  // lands and a few other rule-based cards.
  const oracleMap = window.thestackOracle || {};
  const scry = oracleMap[c.name] || null;

  const cost = c.manaCost || scry?.mana_cost || "";
  const typeLine = scry?.type_line || (c.types?.length ? c.types.join(", ") : "");
  const oracle = c.oracleText || scry?.oracle_text || "";
  if (!cost && !typeLine && !oracle && c.power == null) return null;

  const tip = document.createElement("div");
  tip.className = "card-tooltip";
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

function playerBoardEl(player, zonesByOwner, isActing) {
  const wrap = document.createElement("div");
  wrap.className = "player-board" + (isActing ? " active" : "");

  const h = document.createElement("h3");
  const left = document.createElement("span");
  left.textContent = player.name;
  const right = document.createElement("span");
  right.textContent = `${player.lifeTotal}♥  ·  ${player.handSize} hand  ·  ${player.librarySize} lib`;
  h.appendChild(left);
  h.appendChild(right);
  wrap.appendChild(h);

  const zones = zonesByOwner.get(player.id) || {};
  for (const zt of ["Battlefield", "Hand", "Graveyard", "Exile"]) {
    const cards = zones[zt] || [];
    if (zt === "Exile" && cards.length === 0) continue;
    wrap.appendChild(zoneEl(fmtZoneLabel(zt), cards));
  }
  return wrap;
}

// eslint-disable-next-line no-unused-vars
function renderBoard(panel, obs) {
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
  for (const p of obs.players || []) {
    panel.appendChild(playerBoardEl(p, zonesByOwner, p.id === acting));
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

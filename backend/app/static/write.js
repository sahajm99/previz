/* The Screenwriter canvas.
 *
 * The editor stores a TYPE per block and no styling at all. Margins, the Enter
 * table and the Tab cycle are fetched once from /api/screenplay/grammar, so
 * app/screenplay.py is the only definition of screenplay format in the product
 * and this file cannot drift from the parser.
 *
 * The one contenteditable rule that matters: retyping a block changes its
 * dataset and its class and NEVER its innerHTML. Rewriting the HTML of the block
 * you are typing in destroys the caret, and that is the whole reason editors
 * built this way feel broken.
 */
const $ = (id) => document.getElementById(id);
const api = (p, o) => fetch(p, o).then((r) => r.ok ? r.json()
  : r.json().then((e) => Promise.reject(new Error(e.detail || r.status))));

let G = null;            // the grammar, from the server
let scene = 1;
let cast = [];
let saveTimer = null;
let running = false;
let running_exchange = [];   // elements accumulated across one orchestrated run

/* ------------------------------------------------------------------ layout */

function applyLayout() {
  // The margins are data. This turns LAYOUT into CSS at runtime rather than
  // hard coding a second copy of it in the stylesheet.
  const css = Object.entries(G.layout).map(([type, s]) => {
    const bits = [
      `margin-left:${s.indent}ch`,
      `width:${s.width}ch`,
      `margin-top:${s.space_before}em`,
      s.case === "upper" ? "text-transform:uppercase" : "",
      s.align === "right" ? `text-align:right;margin-left:0;width:${s.width}ch` : "",
    ].filter(Boolean).join(";");
    return `#canvas .el[data-type="${type}"]{${bits}}`;
  }).join("\n");
  const tag = document.createElement("style");
  tag.textContent = css;
  document.head.appendChild(tag);
}

const HINTS = {
  scene_heading: "INT. LOCATION - NIGHT", action: "what the camera sees",
  character: "WHO SPEAKS", parenthetical: "(how)", dialogue: "what they say",
  transition: "CUT TO:", shot: "ANGLE ON",
};

function buildToolbar() {
  const right = $("toolsRight");
  G.elements.forEach((type) => {
    const b = document.createElement("button");
    b.className = "tool";
    b.dataset.type = type;
    b.innerHTML = `${G.layout[type].label}<span class="k">${G.layout[type].key.replace("Ctrl+", "^")}</span>`;
    b.onclick = () => { const el = here(); if (el) { setType(el, type); focusEnd(el); } };
    $("tools").insertBefore(b, right);
  });
}

/* ------------------------------------------------------------------ blocks */

function block(type, text) {
  const d = document.createElement("div");
  d.className = "el";
  d.dataset.type = type;
  d.dataset.label = G.layout[type].label;
  d.dataset.hint = HINTS[type] || "";
  d.textContent = text || "";
  return d;
}

function setType(el, type) {
  // dataset and class only. Never innerHTML: that is what kills the caret.
  el.dataset.type = type;
  el.dataset.label = G.layout[type].label;
  el.dataset.hint = HINTS[type] || "";
  paintToolbar();
}

function blocks() { return [...$("canvas").querySelectorAll(".el")]; }

function here() {
  const s = window.getSelection();
  if (!s || !s.anchorNode) return blocks()[0] || null;
  const n = s.anchorNode;
  const el = n.nodeType === 1 ? n : n.parentElement;
  return el ? el.closest(".el") : null;
}

function focusEnd(el) {
  const r = document.createRange();
  r.selectNodeContents(el);
  r.collapse(false);
  const s = window.getSelection();
  s.removeAllRanges();
  s.addRange(r);
  markHere();
}

function markHere() {
  const cur = here();
  blocks().forEach((b) => b.classList.toggle("here", b === cur));
  paintToolbar();
}

function paintToolbar() {
  const cur = here();
  const t = cur ? cur.dataset.type : null;
  document.querySelectorAll(".tool").forEach((b) =>
    b.classList.toggle("on", b.dataset.type === t));
}

/* ------------------------------------------------- text out, for the server */

function canvasText() {
  // Mirrors screenplay.to_text: a cue, its parenthetical and its speech are one
  // block with no blank line inside, everything else is separated by one.
  let out = "";
  let prev = null;
  for (const b of blocks()) {
    let t = b.textContent.replace(/\s+$/, "");
    if (!t) continue;
    const spec = G.layout[b.dataset.type];
    if (spec.case === "upper") t = t.toUpperCase();
    // An all caps action line would parse back as a character cue, so force it.
    if (b.dataset.type === "action" && t === t.toUpperCase() && /[A-Z]/.test(t)) {
      t = "!" + t;
    }
    const glued = (["character", "parenthetical"].includes(prev)
      && ["dialogue", "parenthetical"].includes(b.dataset.type))
      || (prev === "dialogue" && b.dataset.type === "dialogue");
    out += prev === null ? t : (glued ? "\n" + t : "\n\n" + t);
    prev = b.dataset.type;
  }
  return out;
}

function dirty() {
  $("saved").textContent = "unsaved";
  $("saved").className = "dirty";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(save, 900);
}

async function save(rerender) {
  clearTimeout(saveTimer);
  try {
    const d = await api(`/api/scenes/${scene}/screenplay`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: canvasText() }),
    });
    $("saved").textContent = "saved";
    $("saved").className = "ok";
    $("pages").textContent =
      `${d.stats.pages} pages · ${d.stats.elements} elements · ${d.stats.scenes} scene`;
    if (rerender) render(d.lines);
    loadSceneList();
  } catch (e) {
    $("saved").textContent = "save failed";
    $("saved").className = "dirty";
  }
}

/* ------------------------------------------------------------------ the page */

function render(lines) {
  const c = $("canvas");
  c.innerHTML = "";
  (lines.length ? lines : [{ type: "scene_heading", text: "" }])
    .forEach((l) => c.appendChild(block(l.type, l.text)));
  focusEnd(c.lastChild);
}

function onKey(e) {
  const cur = here();
  if (!cur) return;

  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    const next = G.on_enter[cur.dataset.type] || "action";
    const b = block(next, "");
    cur.after(b);
    focusEnd(b);
    dirty();
    return;
  }
  if (e.key === "Tab") {
    e.preventDefault();
    const cyc = G.tab_cycle;
    const i = cyc.indexOf(cur.dataset.type);
    setType(cur, cyc[(i + (e.shiftKey ? cyc.length - 1 : 1)) % cyc.length]);
    dirty();
    return;
  }
  if ((e.ctrlKey || e.metaKey) && /^[1-7]$/.test(e.key)) {
    e.preventDefault();
    setType(cur, G.elements[Number(e.key) - 1]);
    dirty();
    return;
  }
  if (e.key === "Backspace" && !cur.textContent && blocks().length > 1) {
    e.preventDefault();
    const prev = cur.previousElementSibling;
    cur.remove();
    if (prev) focusEnd(prev);
    dirty();
  }
}

function onInput() {
  const c = $("canvas");
  if (!c.querySelector(".el")) {            // select all then delete
    c.innerHTML = "";
    c.appendChild(block("action", ""));
    focusEnd(c.firstChild);
  }
  const cur = here();
  // Typing a slugline promotes the block, the way every screenplay editor does.
  if (cur && cur.dataset.type === "action"
      && /^(int|ext|est|i\/e)[.\s/]/i.test(cur.textContent)) {
    setType(cur, "scene_heading");
  }
  markHere();
  dirty();
}

function onPaste(e) {
  e.preventDefault();
  const text = (e.clipboardData || window.clipboardData).getData("text/plain");
  if (!text) return;
  const cur = here();
  let at = cur;
  text.split(/\r?\n/).forEach((line) => {
    if (!line.trim()) return;
    const b = block("action", line.trim());
    at ? at.after(b) : $("canvas").appendChild(b);
    at = b;
  });
  // Let the server type the pasted lines, then re-render from its answer. This
  // is the one case where re-rendering is right: the whole point is to find out
  // what the parser made of it.
  save(true);
}

/* ---------------------------------------------------------------- the agents */

function trace(text, cls, agent) {
  // Text nodes, never innerHTML. Most of what lands here is model output, and a
  // violation detail quotes the script back at you, so it is exactly the string
  // you must not hand to an HTML parser.
  const t = $("trace");
  if (t.textContent === "Idle.") t.textContent = "";
  const d = document.createElement("div");
  d.className = "ev " + (cls || "");
  if (agent) {
    const a = document.createElement("span");
    a.className = "ag";
    a.textContent = agent;
    d.append(a, " ");
  }
  d.append(text);
  t.appendChild(d);
  t.scrollTop = t.scrollHeight;
}

async function run(url, body, onEvent) {
  if (running) return;
  running = true;
  document.querySelectorAll(".go").forEach((b) => b.disabled = true);
  try {
    const res = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      // A refusal arrives as JSON, not as a stream. Read it and say so, rather
      // than opening a reader on it and going quiet.
      const d = await res.json().catch(() => ({}));
      trace(d.detail || `request failed with ${res.status}`, "bad");
      return;
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const frames = buf.split("\n\n");
      buf = frames.pop();
      for (const f of frames) {
        const line = f.split("\n").find((l) => l.startsWith("data: "));
        if (line) onEvent(JSON.parse(line.slice(6)));
      }
    }
  } catch (e) {
    trace(`transport failed: ${e.message}`, "bad");
  } finally {
    running = false;
    document.querySelectorAll(".go").forEach((b) => b.disabled = false);
    paintWho();          // last, so it can re-disable Dialogue if nobody is ready
  }
}

function onEvent(ev) {
  if (ev.t === "thinking") trace(ev.text, "", ev.agent);
  else if (ev.t === "context") {
    const n = Object.values(ev.slots).reduce((a, b) => a + b, 0);
    trace(`context assembled · ${n} chars · ${ev.chunk_ids.length} chunks retrieved`
      + (ev.dropped.length ? ` · dropped ${ev.dropped.join(", ")}` : ""), "ctx");
  } else if (ev.t === "violation") trace(`${ev.kind} · ${ev.detail}`, "bad");
  else if (ev.t === "line_ready") {
    card(ev.line);
    // A turn number means this line is part of an orchestrated exchange, so keep
    // its elements to offer the whole thing as one insert when the run ends.
    if (ev.line.turn) {
      if (ev.line.beat) {
        trace(`turn ${ev.line.turn} · ${ev.line.beat}`, "ctx",
              ev.line.character);
      }
      running_exchange.push(...(ev.line.elements || []));
    }
  }
  else if (ev.t === "partial" && ev.field === "action") {
    card({ agent: "ActionWriter", line: ev.text, score: null,
           elements: [{ type: "action", text: ev.text }] });
  } else if (ev.t === "tool_call") trace(`calling ${ev.tool}`);
  else if (ev.t === "tool_result") trace(`${ev.tool} · ${ev.summary}`);
  else if (ev.t === "proposal") {
    // A supervisor finding is a note, not an element. It gets a card with no
    // Insert, because there is nothing here that belongs on the page.
    card({ agent: `finding · ${ev.field}`, line: ev.rationale, score: null,
           elements: [] });
  } else if (ev.t === "data" && ev.locations) {
    // Handled here rather than on the per place event, so a live search and the
    // seeded fallback both render exactly once.
    ev.locations.forEach(locationCard);
    if (ev.live === false) trace("seeded locations, no Maps key configured", "ctx");
  } else if (ev.t === "error") trace(ev.message, "bad");
  else if (ev.t === "run_end") {
    trace(`done in ${ev.ms} ms`);
    // The whole exchange as one card, on top of the per line ones. Inserting four
    // lines should not be four clicks, and the per line cards are still there for
    // when you only want two of them.
    if (running_exchange.length > 2) {
      const lines = running_exchange.filter((e) => e.type === "dialogue").length;
      card({ agent: `the exchange · ${lines} lines`, line: "",
             score: null, elements: running_exchange.slice() });
    }
    running_exchange = [];
  }
}

function card(p) {
  const d = document.createElement("div");
  d.className = "card";
  const badge = p.score == null ? ""
    : `<span class="sc ${p.passed ? "pass" : "fail"}">${p.score.toFixed(3)}</span>`;
  d.innerHTML =
    `<div class="top"><span class="ag"></span>${badge}</div>
     <div class="txt"></div>
     <div class="row"><button class="ins">Insert</button><button class="no">Discard</button></div>`;
  // The agent name carries a character name, so it is set as text, not markup.
  d.querySelector(".ag").textContent = p.agent;
  d.querySelector(".txt").textContent =
    (p.elements || []).map((e) => e.text).join("\n") || p.line;
  const ins = d.querySelector(".ins");
  if ((p.elements || []).length) {
    ins.onclick = () => { insert(p.elements); d.remove(); };
  } else {
    ins.remove();                    // a finding has nothing to put on the page
    d.querySelector(".no").textContent = "Dismiss";
  }
  d.querySelector(".no").onclick = () => d.remove();

  // The feedback loop. Anything a model wrote takes a director's note and goes
  // back to the same writer: the character's own sub-agent for a line, the
  // ActionWriter for a paragraph. The note directs this line only. It never
  // touches the Voice Card, because a note is direction and the card is canon.
  if (p.character_id || p.agent === "ActionWriter") {
    const r = document.createElement("div");
    r.className = "row";
    const inp = document.createElement("input");
    inp.className = "f";
    inp.placeholder = "note · angrier, shorter, she would not admit that";
    inp.style.flex = "2.2";
    const go = document.createElement("button");
    go.className = "ins";
    go.textContent = "Revise";
    const revise = () => {
      const note = inp.value.trim();
      d.remove();
      if (p.character_id) {
        trace(`note to ${p.character}: ${note || "again, differently"}`);
        run(`/api/scenes/${scene}/next-line`,
            { character_id: p.character_id, on_page: canvasText(),
              note: note, previous: p.line }, onEvent);
      } else {
        trace(`note to the ActionWriter: ${note || "again, differently"}`);
        run(`/api/scenes/${scene}/next-action`,
            { intent: $("intent").value, on_page: canvasText(),
              note: note, previous: p.line }, onEvent);
      }
    };
    go.onclick = revise;
    inp.onkeydown = (e) => { if (e.key === "Enter") revise(); };
    r.append(inp, go);
    d.appendChild(r);
  }
  $("cards").prepend(d);
}

function insert(elements) {
  let at = here() || blocks().slice(-1)[0];
  elements.forEach((e) => {
    const b = block(e.type, e.text);
    b.classList.add("ai");     // visible provenance: this line came from an agent
    at ? at.after(b) : $("canvas").appendChild(b);
    at = b;
  });
  if (at) focusEnd(at);
  save();
}

function paintWho() {
  // The picker carries the readiness on its face: a character's Voice Card
  // version and how many facts they know as of this scene, or the core answer
  // count when they are not writable yet.
  const sel = $("who");
  sel.innerHTML = "";
  cast.forEach((c) => {
    const o = document.createElement("option");
    o.value = c.id;
    o.disabled = !c.ready;
    o.textContent = c.ready
      ? `${c.name} · card v${c.canon_version} · knows ${c.knows.length}`
      : `${c.name} · ${c.core_answered}/12 core answers`;
    sel.appendChild(o);
  });
  if (!cast.length) {
    const o = document.createElement("option");
    o.textContent = "nobody is in this scene yet";
    o.disabled = true;
    sel.appendChild(o);
  }
  const ready = cast.filter((c) => c.ready).length;
  $("whoTag").textContent = `${ready} of ${cast.length} ready`;
  $("btnLine").disabled = !ready;
  const first = cast.find((c) => c.ready);
  if (first) sel.value = first.id;
}

/* Each of these writes exactly one element. Two of them never touch the network,
 * and that is deliberate: a slugline and a transition are choices, not
 * generations, and asking a model to guess a location it was not given is how you
 * end up with a scene set somewhere nobody picked. */

function localInsert(type, text) {
  card({ agent: "you", line: text, score: null,
         elements: [{ type: type, text: text }] });
}

function insertSlug() {
  const loc = $("loc").value.trim();
  if (!loc) { $("loc").focus(); return; }
  const tod = $("tod").value;
  localInsert("scene_heading",
              `${$("ie").value} ${loc.toUpperCase()}` + (tod ? ` - ${tod}` : ""));
}

function writeLine() {
  const c = cast.find((x) => x.id === $("who").value);
  if (!c) return;
  trace(`asking ${c.name}'s sub-agent for one line`);
  run(`/api/scenes/${scene}/next-line`,
      { character_id: c.id, on_page: canvasText() }, onEvent);
}

function scoutLocation() {
  const need = $("need").value.trim();
  if (!need) { $("need").focus(); return; }
  trace("scouting real places");
  run("/api/scout",
      { need: need, region: $("region").value, scene: scene }, onEvent);
}

function locationCard(l) {
  // A real place becomes a scene heading, because that is what a location is once
  // it reaches the page. The address stays on the card, not in the script.
  const guess = `${$("ie").value} ${l.name.toUpperCase()}`
    + ($("tod").value ? ` - ${$("tod").value}` : "");
  card({ agent: `Scout · ${l.address || "no address"}`, line: guess, score: null,
         elements: [{ type: "scene_heading", text: guess }] });
}

/* ------------------------------------------------------------------- scenes */

async function loadSceneList() {
  const st = await api("/api/story");
  $("sceneList").innerHTML = "";
  st.scene_index.forEach((s) => {
    const b = document.createElement("button");
    b.className = "scene-btn" + (s.number === scene ? " on" : "");
    b.innerHTML = `<span class="n">SCENE ${s.number}</span><span class="sl"></span>`;
    b.querySelector(".sl").textContent = s.slugline;
    b.onclick = () => loadScene(s.number);
    $("sceneList").appendChild(b);
  });
}

async function loadScene(n) {
  scene = n;
  const d = await api(`/api/scenes/${n}/screenplay`);
  cast = d.cast;
  render(d.lines);
  paintWho();
  $("pages").textContent =
    `${d.stats.pages} pages · ${d.stats.elements} elements`;
  $("saved").textContent = "saved";
  $("saved").className = "ok";
  $("sceneMeta").textContent = d.synopsis || "no synopsis yet";
  $("cards").innerHTML = "";
  loadSceneList();
}

/* --------------------------------------------------------------------- boot */

(async function boot() {
  G = await api("/api/screenplay/grammar");
  applyLayout();
  buildToolbar();
  const c = $("canvas");
  c.addEventListener("keydown", onKey);
  c.addEventListener("input", onInput);
  c.addEventListener("paste", onPaste);
  document.addEventListener("selectionchange", () => {
    if (document.activeElement === c) markHere();
  });
  // The pickers come from the grammar too, so the times of day the editor offers
  // are the ones the parser recognises in a slugline.
  fill($("ie"), G.int_ext);
  fill($("tod"), G.times);
  fill($("trans"), G.transitions);
  $("ie").value = "INT.";
  $("tod").value = "NIGHT";

  $("btnSlug").onclick = insertSlug;
  $("btnLine").onclick = writeLine;
  $("btnExchange").onclick = () => {
    const n = Math.max(1, Math.min(Number($("turns").value) || 4, 12));
    running_exchange = [];
    trace(`directing ${n} turns across ${cast.filter((c) => c.ready).length} `
          + `character sub-agents`);
    run(`/api/scenes/${scene}/exchange`,
        { turns: n, order: "director", brief: $("brief").value,
          on_page: canvasText() }, onEvent);
  };
  $("btnLoc").onclick = scoutLocation;
  $("btnTrans").onclick = () => localInsert("transition", $("trans").value);
  $("btnCheck").onclick = () => {
    trace("supervising the page against canon and the knowledge horizon");
    run(`/api/scenes/${scene}/supervise`, {}, onEvent);
  };
  $("btnAction").onclick = () => {
    trace("asking the ActionWriter for one paragraph");
    run(`/api/scenes/${scene}/next-action`,
        { intent: $("intent").value, on_page: canvasText() }, onEvent);
  };
  await loadScene(1);
})();

function fill(sel, values) {
  values.forEach((v) => {
    const o = document.createElement("option");
    o.value = v;
    o.textContent = v;
    sel.appendChild(o);
  });
}

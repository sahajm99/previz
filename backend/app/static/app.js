/* Magic Hour · the client.
 *
 * One SSE parser for every surface, because the backend sends one envelope for
 * every stream. That is the reason there is no spinner anywhere in this app: a
 * spinner says something is happening, and these events say what is happening.
 *
 * State is the server's. This refetches /api/story after any write rather than
 * maintaining a client side copy, which at this size is cheaper than keeping two
 * versions of the truth in sync and is the bug class we are avoiding everywhere
 * else in the product.
 */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
/* Maps URLs come from the Places API, so they are third party data going into an
 * href. Escaping alone would still let a javascript: scheme through, so the
 * scheme is checked rather than assumed. */
const safeUrl = (u) => /^https?:\/\//i.test(String(u ?? "")) ? esc(u) : "#";

let STORY = null;
let SURFACE = "bible";
let PICKED = null;      // selected character id, Cast surface

/* ------------------------------------------------------------------ the field */

(function stars() {
  const c = $("#stars"), x = c.getContext("2d");
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  let pts = [];
  const size = () => {
    c.width = innerWidth; c.height = innerHeight;
    pts = Array.from({ length: Math.round(innerWidth * innerHeight / 9000) }, () => ({
      x: Math.random() * c.width, y: Math.random() * c.height,
      r: Math.random() * 1.05 + 0.2, p: Math.random() * Math.PI * 2,
      s: Math.random() * 0.0007 + 0.00016,
    }));
  };
  addEventListener("resize", size); size();
  // Slow on purpose. The background exists to give the page depth, not to be
  // looked at, so nothing here moves fast enough to pull the eye off the work.
  (function tick(t) {
    x.clearRect(0, 0, c.width, c.height);
    for (const p of pts) {
      const a = 0.26 + 0.5 * (0.5 + 0.5 * Math.sin(p.p + t * p.s));
      x.globalAlpha = a;
      x.fillStyle = p.y / c.height > 0.72 ? "#F7D9A6" : "#DCE6FF";
      x.beginPath(); x.arc(p.x, p.y, p.r, 0, 7); x.fill();
    }
    requestAnimationFrame(tick);
  })(0);
})();

/* ------------------------------------------------------------------ transport */

async function api(path, opts) {
  const r = await fetch("/api" + path, {
    ...opts,
    headers: opts?.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 300)}`);
  return r.json();
}

/* One reader for every stream. `on` maps an event type to a handler. */
async function sse(path, body, on = {}) {
  trace("run", "starting", "think");
  const r = await fetch("/api" + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) { trace("error", `${r.status} ${await r.text()}`, "err"); return; }
  const rd = r.body.getReader(), dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await rd.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop();
    for (const p of parts) {
      const line = p.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      let e; try { e = JSON.parse(line.slice(6)); } catch { continue; }
      render(e);
      on[e.t]?.(e);
    }
  }
}

/* Every event, drawn in the Trace tab. Surfaces add to this, never replace it. */
function render(e) {
  switch (e.t) {
    case "run_start": trace(e.agent || "run", "started", "think"); break;
    case "thinking": trace(e.agent, e.text, "think"); break;
    case "tool_call": trace(e.tool, JSON.stringify(e.args ?? {}), "tool"); break;
    case "tool_result": trace(e.tool, e.summary, "tool"); break;
    case "context": showContext(e); break;
    case "partial": trace(e.field, e.text, "think"); break;
    case "violation": trace(e.kind, e.detail, "viol"); break;
    case "proposal": trace("proposal", e.rationale, "viol"); loadCanon(); break;
    case "error": trace("error", e.message + (e.retryable ? " · retryable" : ""), "err"); break;
    case "run_end": trace("run_end", `${e.ms} ms`, "done"); break;
  }
}

function trace(k, v, cls = "") {
  const box = $("#trace");
  if (box.classList.contains("faint")) { box.classList.remove("faint"); box.innerHTML = ""; }
  const d = document.createElement("div");
  d.className = "ev " + cls;
  d.innerHTML = `<div class="k">${esc(k)}</div><div class="v">${esc(v)}</div>`;
  box.append(d);
  box.scrollTop = box.scrollHeight;
}

function showContext(e) {
  const total = Object.values(e.slots).reduce((a, b) => a + b, 0) || 1;
  $("#slots").classList.remove("faint");
  $("#slots").innerHTML = Object.entries(e.slots).map(([k, v]) => `
    <div class="slot">
      <div class="row"><b>${k}</b><span>${v}</span></div>
      <div class="meter"><i style="width:${(v / total * 100).toFixed(1)}%"></i></div>
    </div>`).join("") +
    `<div class="tiny faint" style="margin-top:8px">${total} chars · ${e.chunk_ids.length} chunk(s)` +
    (e.dropped?.length ? ` · dropped ${e.dropped.join(", ")}` : "") + `</div>`;
}

/* --------------------------------------------------------------- navigation */

$$(".tab[data-s]").forEach((t) => t.onclick = () => {
  SURFACE = t.dataset.s;
  $$(".tab[data-s]").forEach((x) => x.classList.toggle("on", x === t));
  $$(".surface").forEach((s) => s.classList.toggle("on", s.dataset.s === SURFACE));
  const tint = getComputedStyle(t).getPropertyValue("--tint");
  document.documentElement.style.setProperty("--accent", tint.trim());
});

$$("#itabs button").forEach((b) => b.onclick = () => {
  $$("#itabs button").forEach((x) => x.classList.toggle("on", x === b));
  $$(".ipane").forEach((p) => p.classList.toggle("on", p.dataset.i === b.dataset.i));
});

/* ------------------------------------------------------------------ the bible */

async function load() {
  STORY = await api("/story");
  $("#title").textContent = STORY.title;
  $("#logline").textContent = STORY.logline;
  const c = STORY.counts;
  $("#chunks").textContent =
    `${c.characters} cast · ${c.scenes} scenes · ${c.shots} shots · ${c.locations} loc`;
  $("#spine").innerHTML =
    `<div style="margin-bottom:8px">${esc(STORY.summary || STORY.logline)}</div>` +
    Object.entries(STORY.style || {}).map(([k, v]) =>
      `<div class="tiny"><span class="faint mono">${k}</span> ${esc(v)}</div>`).join("");

  $("#sceneCards").innerHTML = STORY.scenes.map((s) => `
    <div class="panel pad">
      <div class="mono tiny" style="color:var(--board)">${esc(s.slugline)}</div>
      <div class="tiny muted" style="margin:7px 0 9px">${esc(s.synopsis)}</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        <span class="chip">scene ${s.number}</span>
        <span class="chip ${s.status === "written" ? "ok" : ""}">${s.status}</span>
        <span class="chip">${s.shots.length} shots</span>
      </div>
    </div>`).join("");

  $("#charCards").innerHTML = STORY.characters.map(cardFor).join("");
  fillScenes();
  drawCastList();
  drawBoard();
  drawLocs();
  drawPages();
  loadCanon();
}

function cardFor(ch) {
  const pct = Math.round(ch.core_answered / 12 * 100);
  return `
    <div class="panel pad">
      <div style="display:flex;gap:11px;align-items:center">
        ${ch.sheet_url
          ? `<img src="${ch.sheet_url}" style="width:52px;height:52px;object-fit:cover;
               object-position:12% 18%;border-radius:9px;border:1px solid var(--line)">`
          : `<div style="width:52px;height:52px;border-radius:9px;border:1px dashed var(--line);
               display:grid;place-items:center;color:var(--ink-faint);font-size:17px">${esc(ch.name[0])}</div>`}
        <div style="flex:1">
          <div style="font-weight:500">${esc(ch.name)}</div>
          <div class="tiny faint">${esc(ch.role)} · canon v${ch.canon_version}</div>
        </div>
      </div>
      <div style="margin:11px 0 7px" class="meter"><i style="width:${pct}%"></i></div>
      <div class="tiny faint">${ch.core_answered} of 12 core · ${ch.answer_count} answered</div>
      <div style="display:flex;gap:6px;margin-top:9px;flex-wrap:wrap">
        <span class="chip ${ch.has_identity_card ? "ok" : ""}">identity</span>
        <span class="chip ${ch.has_voice_card ? "ok" : ""}">voice</span>
        <span class="chip ${ch.ready_for_dialogue ? "ok" : "warn"}">
          ${ch.ready_for_dialogue ? "can speak" : "needs answers"}</span>
      </div>
    </div>`;
}

function fillScenes() {
  const opts = STORY.scenes.map((s) =>
    `<option value="${s.number}">${s.number}. ${esc(s.slugline)}</option>`).join("");
  for (const id of ["#scScene", "#bdScene"]) $(id).innerHTML = opts;
  $("#scoutScene").innerHTML = `<option value="">nothing</option>` + opts;
}

let searchTimer;
$("#q").oninput = (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  if (!q) return;
  searchTimer = setTimeout(async () => {
    const { hits } = await api(`/bible/search?q=${encodeURIComponent(q)}&k=10`);
    $("#hits").classList.remove("faint");
    $("#hits").innerHTML = hits.length ? hits.map((h) => `
      <div class="hit">
        <div class="meta">
          <span class="lay ${h.layer}">${h.layer}</span>
          <span class="tiny faint">${h.entity_type} · ${h.score.toFixed(4)}</span>
        </div>
        <div class="t">${esc(h.text.slice(0, 260))}</div>
      </div>`).join("") : `<div class="faint tiny">Nothing matched.</div>`;
    $$("#itabs button")[0].click();
  }, 260);
};

/* ------------------------------------------------------------------- the board */

function drawBoard() {
  const n = +$("#bdScene").value || STORY.scenes[0]?.number;
  const sc = STORY.scenes.find((s) => s.number === n);
  const box = $("#board");
  if (!sc?.shots.length) {
    box.innerHTML = `<div class="empty">No shots planned for scene ${n}.
      Plan the list first, then render. Text is free and frames are not.</div>`;
    return;
  }
  box.innerHTML = sc.shots.map(frameFor).join("");
  $$("#board .regen").forEach((b) => b.onclick = () => renderOne(b.dataset.id));
}

function frameFor(sh) {
  const scores = Object.values(sh.face_scores || {}).filter((v) => v != null);
  const best = scores.length ? Math.max(...scores) : null;
  const col = best == null ? "var(--ink-faint)"
    : best >= 0.35 ? "var(--ok)" : "var(--bad)";
  return `
    <div class="frame" id="f-${sh.id}">
      <div class="img">
        ${sh.image_url ? `<img src="${sh.image_url}" alt="shot ${sh.number}">`
          : `<div style="display:grid;place-items:center;height:100%;color:var(--ink-faint);
               font:500 12px var(--mono)">${sh.status}</div>`}
        ${sh.image_url ? `<div class="badge">
            <span class="dot" style="background:${col}"></span>
            ${best == null ? "no score" : best.toFixed(3)}
            ${sh.attempts > 1 ? `<span class="faint">· ${sh.attempts} tries</span>` : ""}
          </div>` : ""}
      </div>
      <div class="slug">
        <span style="color:var(--board)">${sh.number}</span>
        <span>${esc(sh.shot_size)} · ${esc(sh.angle)} · ${esc(sh.lens)} · ${esc(sh.movement)}</span>
        <button class="act regen" data-id="${sh.id}" style="margin-left:auto;padding:3px 8px;font-size:11px">
          regenerate</button>
      </div>
      <div class="desc">${esc(sh.description)}</div>
    </div>`;
}

$("#bdScene").onchange = drawBoard;

$("#btnPlan").onclick = async () => {
  const n = +$("#bdScene").value;
  $("#btnPlan").disabled = true;
  $("#board").innerHTML = `<div class="empty">Cinematographer is breaking scene ${n} down.</div>`;
  await sse(`/scenes/${n}/shots`, { count: +$("#bdCount").value }, {
    data: async () => { await load(); },
  });
  $("#btnPlan").disabled = false;
};

$("#btnRender").onclick = async () => {
  const n = +$("#bdScene").value;
  $("#btnRender").disabled = true;
  await sse(`/scenes/${n}/render`, { style: $("#bdStyle").value }, {
    shot_ready: (e) => paint(e.shot),
    run_end: () => { load(); budget(); },
  });
  $("#btnRender").disabled = false;
};

async function renderOne(id) {
  const el = $(`#f-${id} .img`);
  if (el) el.insertAdjacentHTML("beforeend", `<div class="shimmer"></div>`);
  await sse(`/shots/${id}/render`, { style: $("#bdStyle").value }, {
    shot_ready: (e) => paint(e.shot),
    run_end: () => { budget(); },
  });
}

/* Frames bloom in as they land. This is the only expressive animation here, and
 * it is the moment the product is about, so it gets the one. */
function paint(sh) {
  const old = $(`#f-${sh.id}`);
  const html = frameFor(sh);
  if (old) old.outerHTML = html;
  else $("#board").insertAdjacentHTML("beforeend", html);
  const fresh = $(`#f-${sh.id}`);
  fresh?.classList.add("new");
  $$(`#f-${sh.id} .regen`).forEach((b) => b.onclick = () => renderOne(b.dataset.id));
}

async function budget() {
  try {
    const b = await api("/board/budget");
    $("#budget").textContent = `${b.spent}/${b.cap} images`;
    $("#budget").className = "chip mono " + (b.spent >= b.cap ? "bad" : b.spent ? "warn" : "");
  } catch {}
}

/* ------------------------------------------------------------------ the script */

function drawPages() {
  const n = +$("#scScene").value || STORY.scenes[0]?.number;
  const sc = STORY.scenes.find((s) => s.number === n);
  if (!sc) return;
  const names = STORY.characters.map((c) => c.name.toUpperCase());
  const body = (sc.body || "").split("\n").map((l) => {
    const t = l.trim();
    if (!t) return "";
    if (names.includes(t)) return `<span class="cue">${esc(t)}</span>`;
    const prev = t.toUpperCase() === t && t.length < 40;
    return prev ? `<span class="cue">${esc(t)}</span>`
      : `<span class="dlg">${esc(t)}</span>`;
  }).join("");
  $("#pages").innerHTML = `<span class="head">${esc(sc.slugline)}</span>` +
    (body || `<span class="act faint">Nothing written yet. ${esc(sc.synopsis)}</span>`);
}

$("#scScene").onchange = drawPages;

$("#btnDialogue").onclick = async () => {
  const n = +$("#scScene").value;
  $("#btnDialogue").disabled = true;
  $("#lines").innerHTML = "";
  await sse(`/scenes/${n}/dialogue`, { turns: 4, brief: $("#scIntent").value || "" }, {
    line_ready: (e) => addLine(e.line),
    run_end: () => load(),
  });
  $("#btnDialogue").disabled = false;
};

$("#btnAction").onclick = async () => {
  const n = +$("#scScene").value;
  $("#btnAction").disabled = true;
  await sse(`/scenes/${n}/action`, { intent: $("#scIntent").value || "" },
            { run_end: () => load() });
  $("#btnAction").disabled = false;
};

$("#btnSupervise").onclick = async () => {
  $("#btnSupervise").disabled = true;
  $$("#itabs button")[1].click();
  await sse(`/scenes/${+$("#scScene").value}/supervise`, {}, { run_end: loadCanon });
  $("#btnSupervise").disabled = false;
};

function addLine(ln) {
  const ok = ln.passed !== false;
  $("#lines").insertAdjacentHTML("beforeend", `
    <div class="panel pad" style="margin-bottom:9px;animation:rise 240ms var(--ease)">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span class="mono tiny" style="letter-spacing:.06em">${esc(ln.character.toUpperCase())}</span>
        <span class="chip ${ok ? "ok" : "bad"}">${ln.score == null ? "no score" : ln.score.toFixed(3)}</span>
      </div>
      <div class="tiny" style="margin-top:7px;line-height:1.5">${esc(ln.line)}</div>
      ${ok ? "" : `<div class="tiny" style="color:var(--bad);margin-top:6px">${esc(ln.reason)}</div>`}
    </div>`);
}

/* -------------------------------------------------------------------- the cast */

function drawCastList() {
  $("#castList").innerHTML = STORY.characters.map((c) => `
    <button class="panel pad pick" data-id="${c.id}" style="display:block;width:100%;
      text-align:left;margin-bottom:8px;cursor:pointer;color:inherit;
      border-color:${c.id === PICKED ? "color-mix(in srgb,var(--accent) 50%,transparent)" : "var(--line)"}">
      <div style="font-weight:500">${esc(c.name)}</div>
      <div class="tiny faint">${c.core_answered}/12 core · ${c.answer_count} answers</div>
    </button>`).join("");
  $$("#castList .pick").forEach((b) => b.onclick = () => pick(b.dataset.id));
  if (PICKED) pick(PICKED);
}

async function pick(id) {
  PICKED = id;
  drawCastList();
  const ch = await api(`/characters/${id}`);
  $("#castWho").textContent = ch.name;
  const p = ch.progress;
  $("#castDetail").innerHTML = `
    <div class="panel pad" style="margin-bottom:12px">
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:11px">
        <button class="act primary" id="btnCompile">Compile cards</button>
        <button class="act" id="btnCast">Cast · sheet and fingerprint</button>
        <span class="chip">${p.answered}/100</span>
        <span class="chip ${p.ready_for_dialogue ? "ok" : "warn"}">
          ${p.core_done}/12 core</span>
      </div>
      <div class="grid" style="grid-template-columns:repeat(7,1fr);gap:6px">
        ${Object.entries(p.by_part).map(([, v]) => `
          <div>
            <div class="meter"><i style="width:${v.done / v.total * 100}%"></i></div>
            <div class="tiny faint" style="margin-top:5px;font-size:10px">
              ${esc(v.label)}<br>${v.done}/${v.total}</div>
          </div>`).join("")}
      </div>
    </div>
    ${ch.identity_card ? cardPanel("Identity Card · frozen, pasted verbatim into every image prompt", [
      ["descriptor", ch.identity_card.descriptor],
      ["wardrobe", ch.identity_card.wardrobe],
      ["never", ch.identity_card.negative]], ch.sheet_url) : ""}
    ${ch.voice_card ? cardPanel("Voice Card · frozen, injected verbatim into her sub-agent", [
      ["how she speaks", ch.voice_card.card],
      ["says", (ch.voice_card.phrases || []).join(" · ")],
      ["never says", (ch.voice_card.never_says || []).join(" · ")],
      ["samples", (ch.voice_card.samples || []).map((s) => `"${s}"`).join("\n")],
      ...Object.entries(ch.voice_card.register || {})]) : ""}
    <div class="panel pad">
      <div class="eyebrow">Next questions</div>
      ${ch.next_questions.map((q) => `
        <div style="margin-bottom:9px">
          <label class="lab">${q.is_core ? "core · " : ""}${esc(q.part_label)}</label>
          <div class="tiny" style="margin-bottom:5px">${esc(q.text)}</div>
          <input class="f ans" data-q="${esc(q.text)}" placeholder="answer in her words">
        </div>`).join("")}
      <button class="act" id="btnSave">Save answers</button>
      <div class="tiny faint" style="margin-top:7px">
        Saving bumps canon version and stales both cards, so the next call
        recompiles. That is the point: a corrected fact that did not force a
        recompile would keep producing the old face and the old voice.
      </div>
    </div>`;

  $("#btnSave").onclick = async () => {
    const answers = {};
    $$("#castDetail .ans").forEach((i) => { if (i.value.trim()) answers[i.dataset.q] = i.value.trim(); });
    if (!Object.keys(answers).length) return;
    await api(`/characters/${id}/answers`, { method: "PUT", body: JSON.stringify({ answers }) });
    await load(); pick(id);
  };
  $("#btnCompile").onclick = async () => {
    $("#btnCompile").disabled = true;
    $$("#itabs button")[1].click();
    await sse(`/characters/${id}/compile`, {}, { run_end: async () => { await load(); pick(id); } });
  };
  $("#btnCast").onclick = async () => {
    $("#btnCast").disabled = true;
    $$("#itabs button")[1].click();
    await sse(`/characters/${id}/cast`, {}, { run_end: async () => { await load(); pick(id); } });
  };
}

function cardPanel(title, rows, sheet) {
  return `
    <div class="panel lit pad" style="margin-bottom:12px">
      <div class="eyebrow">${esc(title)}</div>
      <div style="display:flex;gap:14px">
        ${sheet ? `<img src="${sheet}" style="width:150px;border-radius:9px;
          border:1px solid var(--line);align-self:flex-start">` : ""}
        <div style="flex:1">
          ${rows.filter(([, v]) => v).map(([k, v]) => `
            <div style="margin-bottom:8px">
              <div class="tiny faint mono">${esc(k)}</div>
              <div class="tiny" style="white-space:pre-wrap;line-height:1.5">${esc(v)}</div>
            </div>`).join("")}
        </div>
      </div>
    </div>`;
}

$("#btnAddChar").onclick = async () => {
  const name = $("#newName").value.trim();
  if (!name) return;
  const ch = await api("/characters", { method: "POST", body: JSON.stringify({ name }) });
  $("#newName").value = "";
  await load(); pick(ch.id);
};

/* ------------------------------------------------------------------- the scout */
let currentScoutSub = 'all';
let currentCanvasBoard = null;

function switchScoutSub(sub) {
  currentScoutSub = sub;
  const btnAll = $("#subAllBtn");
  const btnShort = $("#subShortBtn");
  const btnCanvas = $("#subCanvasBtn");
  if (btnAll) btnAll.className = sub === 'all' ? "act primary" : "act";
  if (btnShort) btnShort.className = sub === 'shortlist' ? "act primary" : "act";
  if (btnCanvas) btnCanvas.className = sub === 'canvas' ? "act primary" : "act";

  if (sub === 'canvas') {
    $("#locs").style.display = "none";
    $("#canvasView").style.display = "block";
    loadScoutCanvas();
  } else {
    $("#locs").style.display = "grid";
    $("#canvasView").style.display = "none";
    drawLocs();
  }
}
window.switchScoutSub = switchScoutSub;
window.loadScoutCanvas = loadScoutCanvas;

function drawLocs(customList = null) {
  const allLocs = STORY && STORY.locations ? STORY.locations : [];
  const shortLocs = allLocs.filter(l => l.shortlisted);
  if ($("#countAll")) $("#countAll").textContent = allLocs.length;
  if ($("#countShort")) $("#countShort").textContent = shortLocs.length;

  let list = customList || (currentScoutSub === 'shortlist' ? shortLocs : allLocs);
  if (!list.length) {
    $("#locs").innerHTML = `<div class="empty">${customList ? "No similar locations found." : (currentScoutSub === 'shortlist' ? "No shortlisted locations yet. Click '☆ Shortlist' on any card to add it." : "No locations yet. Describe a vibe above and click 'Scout Vibe'.")}</div>`;
    return;
  }

  $("#locs").innerHTML = list.map((l) => {
    const scorePct = l.vibe_match_score ? `${Math.round(l.vibe_match_score * 100)}% Vibe Match` : "88% Vibe Match";
    const badgeColor = l.vibe_match_score && l.vibe_match_score > 0.8 ? "var(--emerald, #10B981)" : "var(--amber, #F59E0B)";
    const photo = (l.photos && l.photos.length && (typeof l.photos[0] === 'string' ? l.photos[0] : l.photos[0].url)) || l.photo_url || l.street_view_url || null;
    const imgHtml = photo ? `<a href="${safeUrl(l.maps_url)}" target="_blank" rel="noreferrer" style="display:block;height:150px;margin:-14px -14px 12px -14px;overflow:hidden;border-radius:6px 6px 0 0;background:#000;text-decoration:none;position:relative" title="Click image to open real location on Google Maps"><img src="${safeUrl(photo)}" style="width:100%;height:100%;object-fit:cover;transition:transform 0.2s" alt="${esc(l.name)}" onerror="this.parentElement.style.display='none'"/><span style="position:absolute;bottom:6px;right:6px;background:rgba(0,0,0,0.7);color:#fff;font-size:10px;padding:2px 6px;border-radius:4px;display:flex;align-items:center;gap:3px">🗺️ Google Maps ↗</span></a>` : "";

    const scenesHtml = STORY && STORY.scenes && STORY.scenes.length ? `
      <div style="margin:8px 0;padding:8px;background:rgba(255,255,255,0.03);border-radius:6px;border:1px solid var(--line, rgba(255,255,255,0.08))">
        <div class="tiny faint" style="margin-bottom:6px;font-weight:600">🎬 Attach to Scenes (Click to toggle multiple):</div>
        <div style="display:flex;gap:5px;flex-wrap:wrap">
          ${STORY.scenes.map(s => {
            const isAtt = (l.attached_scenes && l.attached_scenes.includes(s.number)) || (s.location_ids && s.location_ids.includes(l.id));
            return `<button class="act toggle-scene ${isAtt ? 'primary' : ''}" data-lid="${l.id}" data-sc="${s.number}" style="padding:3px 8px;font-size:11px;border-radius:4px">${isAtt ? '★ ' : ''}Scene ${s.number}</button>`;
          }).join("")}
        </div>
      </div>` : "";

    return `
    <div class="panel pad" style="position:relative;display:flex;flex-direction:column;justify-content:space-between">
      <div>
        ${imgHtml}
        <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px">
          <a href="${safeUrl(l.maps_url)}" target="_blank" rel="noreferrer" style="font-weight:600;font-size:16px;color:var(--text, #fff);text-decoration:none;display:flex;align-items:center;gap:4px" title="Open on Google Maps">${esc(l.name)} <span style="font-size:11px;color:var(--accent, #6366F1)">↗ Maps</span></a>
          <span class="chip" style="background:${badgeColor};color:#000;font-weight:700;border:none;padding:2px 8px">${scorePct}</span>
        </div>
        <div class="tiny faint" style="margin-bottom:8px">${esc(l.address)}</div>
        ${l.vibe_reasoning ? `<div class="tiny" style="margin-bottom:8px;padding:6px 8px;background:rgba(255,255,255,0.04);border-left:2px solid ${badgeColor};border-radius:4px;font-style:italic">"${esc(l.vibe_reasoning)}"</div>` : (l.notes ? `<div class="tiny muted" style="margin-bottom:8px">${esc(l.notes)}</div>` : "")}
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px">
          <span class="chip mono" style="background:var(--panel2, rgba(255,255,255,0.05))">Budget: ${esc(l.budget_tier || "Low")}</span>
          <span class="chip mono" style="background:var(--panel2, rgba(255,255,255,0.05))">Permit: ${esc(l.permit_status || "Required")}</span>
        </div>
        ${scenesHtml}
      </div>
      <div style="display:flex;gap:7px;margin-top:auto;padding-top:10px;border-top:1px solid var(--line, rgba(255,255,255,0.1));align-items:center;flex-wrap:wrap">
        <button class="act short ${l.shortlisted ? "primary" : ""}" data-id="${l.id}" data-on="${!l.shortlisted}" style="padding:4px 10px;font-size:12px;font-weight:600">
          ${l.shortlisted ? "★ Shortlisted" : "☆ Shortlist"}</button>
        <button class="act sim" data-id="${l.id}" style="padding:4px 10px;font-size:12px">🔍 Similar</button>
        <button class="act add-canvas" data-id="${l.id}" data-name="${esc(l.name)}" style="padding:4px 10px;font-size:12px">📍 Add to Canvas</button>
        <a class="act" href="${safeUrl(l.maps_url)}" target="_blank" rel="noreferrer" style="margin-left:auto;padding:4px 10px;font-size:12px;text-decoration:none;background:rgba(255,255,255,0.07);color:#fff;border-radius:4px">🗺️ Open Map</a>
      </div>
    </div>`;
  }).join("");

  $$("#locs .short").forEach((b) => b.onclick = async () => {
    await api(`/locations/${b.dataset.id}`, {
      method: "PATCH", body: JSON.stringify({ shortlisted: b.dataset.on === "true" }) });
    load();
  });
  $$("#locs .toggle-scene").forEach((b) => b.onclick = async () => {
    const lid = b.dataset.lid;
    const scNum = +b.dataset.sc;
    if ($("#scoutStatus")) $("#scoutStatus").textContent = `Toggling Scene ${scNum} attachment...`;
    try {
      await api(`/locations/${lid}/toggle-scene`, {
        method: "POST", body: JSON.stringify({ scene: scNum }) });
      if ($("#scoutStatus")) $("#scoutStatus").textContent = `Updated Scene ${scNum} attachment!`;
      await load();
    } catch(e) {
      if ($("#scoutStatus")) $("#scoutStatus").textContent = "Failed to toggle scene attachment.";
    }
  });
  $$("#locs .sim").forEach((b) => b.onclick = async () => {
    const locId = b.dataset.id;
    if ($("#scoutStatus")) $("#scoutStatus").textContent = "Finding similar locations...";
    try {
      const results = await api("/scout/similar", {
        method: "POST", body: JSON.stringify({ place_id: locId, limit: 3 }) });
      if ($("#scoutStatus")) $("#scoutStatus").textContent = `Found ${results.length} visually & semantically similar locations.`;
      drawLocs(results);
    } catch (e) {
      if ($("#scoutStatus")) $("#scoutStatus").textContent = "Similarity search failed or fallback used.";
    }
  });
  $$("#locs .add-canvas").forEach((b) => b.onclick = async () => {
    await addLocToCanvas(b.dataset.id, b.dataset.name);
  });
}

async function addLocToCanvas(locId, name) {
  if (!currentCanvasBoard) await loadScoutCanvas();
  const existing = currentCanvasBoard.nodes.find(n => n.location_id === locId);
  if (existing) {
    switchScoutSub('canvas');
    return;
  }
  const newNode = {
    id: "node_" + Math.random().toString(36).substring(2, 7),
    location_id: locId,
    label: name || "Location",
    node_type: "location",
    x: 150 + (currentCanvasBoard.nodes.length * 40) % 500,
    y: 100 + (currentCanvasBoard.nodes.length * 40) % 300,
    data: { budget: "Low" }
  };
  currentCanvasBoard.nodes.push(newNode);
  if ($("#scoutStatus")) $("#scoutStatus").textContent = `Added "${name}" to Scene Canvas!`;
  await api("/scout/canvas", { method: "POST", body: JSON.stringify(currentCanvasBoard) });
  switchScoutSub('canvas');
}

async function loadScoutCanvas() {
  try {
    currentCanvasBoard = await api("/scout/canvas");
    renderScoutCanvas(currentCanvasBoard);
  } catch (e) {
    if ($("#canvasBoardArea")) $("#canvasBoardArea").innerHTML = `<div class="empty">Failed to load Scene Canvas layout.</div>`;
  }
}

let connectSourceNodeId = null;

window.openCanvasRouteOnMaps = function() {
  if (!currentCanvasBoard || !currentCanvasBoard.nodes) return;
  const locNodes = currentCanvasBoard.nodes.filter(n => n.node_type === "location" && n.label);
  if (!locNodes.length) {
    alert("No location nodes on canvas to route!");
    return;
  }
  const queryParts = locNodes.map(n => encodeURIComponent(n.label));
  const url = "https://www.google.com/maps/dir/" + queryParts.join("/") + "/?entry=ttu";
  window.open(url, "_blank");
};

function renderScoutCanvas(board) {
  const area = $("#canvasBoardArea");
  if (!area || !board) return;
  const nodes = board.nodes || [];
  const conns = board.connections || [];
  if (!nodes.length) {
    area.innerHTML = `<div class="empty">Canvas is empty. Click "📍 Add to Canvas" on any location card!</div>`;
    if ($("#canvasLogisticsText")) $("#canvasLogisticsText").textContent = "No locations on canvas.";
    return;
  }
  let html = `<div style="position:absolute;top:0;left:0;right:0;bottom:0;pointer-events:none">
    <svg width="100%" height="100%" style="position:absolute;top:0;left:0;overflow:visible">
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--accent, #6366F1)"/>
        </marker>
      </defs>`;
  conns.forEach(c => {
    const n1 = nodes.find(n => n.id === c.from_node_id);
    const n2 = nodes.find(n => n.id === c.to_node_id);
    if (n1 && n2) {
      const x1 = n1.x + 85, y1 = n1.y + 40, x2 = n2.x + 85, y2 = n2.y + 40;
      html += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="var(--accent, #6366F1)" stroke-width="2" stroke-dasharray="4" marker-end="url(#arrow)"/>`;
      html += `<text x="${(x1+x2)/2}" y="${(y1+y2)/2 - 8}" fill="var(--muted, #9CA3AF)" font-size="11" font-weight="600" text-anchor="middle" style="background:#000;padding:2px;border-radius:3px">${esc(c.travel_time_min || 15)} min / $${esc(c.logistics_cost_usd || 150)}</text>`;
    }
  });
  html += `</svg></div>`;
  nodes.forEach(n => {
    const isLoc = n.node_type === "location";
    const bg = isLoc ? "var(--panel, #131A27)" : "var(--panel2, #192335)";
    const border = connectSourceNodeId === n.id ? "var(--emerald, #10B981)" : (isLoc ? "var(--accent, #6366F1)" : "var(--board, #10B981)");
    const shadow = connectSourceNodeId === n.id ? "0 0 16px var(--emerald, #10B981)" : "0 4px 12px rgba(0,0,0,0.4)";
    const icon = isLoc ? "📍" : "🎬";
    html += `
    <div class="canvas-node panel pad" data-id="${n.id}" style="position:absolute;left:${n.x}px;top:${n.y}px;width:175px;background:${bg};border:2px solid ${border};border-radius:8px;cursor:move;user-select:none;box-shadow:${shadow};z-index:10;padding:10px;transition:box-shadow 0.2s, border-color 0.2s">
      <div style="font-weight:600;font-size:13px;display:flex;align-items:center;gap:5px;margin-bottom:4px">${icon} <span>${esc(n.label)}</span></div>
      <div class="tiny faint">${isLoc ? "Location Node" : "Scene Beat"}</div>
      <div style="display:flex;justify-content:space-between;margin-top:10px;padding-top:6px;border-top:1px solid rgba(255,255,255,0.1);align-items:center">
        <button class="act conn-node ${connectSourceNodeId === n.id ? 'primary' : ''}" data-id="${n.id}" style="padding:3px 7px;font-size:10px;border-radius:4px;font-weight:600">${connectSourceNodeId === n.id ? '📍 Target...' : '🔗 Connect Order'}</button>
        <button class="act del-node" data-id="${n.id}" style="padding:3px 7px;font-size:11px;color:var(--bad, #EF4444);font-weight:bold;border-radius:4px;background:rgba(239,68,68,0.1)" title="Delete from Canvas">🗑️ Delete</button>
      </div>
    </div>`;
  });
  area.innerHTML = html;
  let totalTime = 0, totalCost = 0;
  conns.forEach(c => { totalTime += (c.travel_time_min || 15); totalCost += (c.logistics_cost_usd || 150); });
  if ($("#canvasLogisticsText")) {
    $("#canvasLogisticsText").textContent = `${nodes.length} nodes connected. Total Est. Transit: ${totalTime} mins | Logistics Budget Impact: $${totalCost}`;
  }

  $$("#canvasBoardArea .conn-node").forEach(b => b.onclick = async (e) => {
    e.stopPropagation();
    const nodeId = b.dataset.id;
    if (!connectSourceNodeId) {
      connectSourceNodeId = nodeId;
      renderScoutCanvas(currentCanvasBoard);
      if ($("#canvasLogisticsText")) $("#canvasLogisticsText").textContent = "Connection mode: Click '🔗 Connect Order' on another node to draw line & set order.";
    } else if (connectSourceNodeId === nodeId) {
      connectSourceNodeId = null;
      renderScoutCanvas(currentCanvasBoard);
      if ($("#canvasLogisticsText")) $("#canvasLogisticsText").textContent = "Connection cancelled.";
    } else {
      currentCanvasBoard.connections.push({
        from_node_id: connectSourceNodeId,
        to_node_id: nodeId,
        travel_time_min: 15,
        logistics_cost_usd: 150
      });
      const fromN = currentCanvasBoard.nodes.find(n => n.id === connectSourceNodeId);
      const toN = currentCanvasBoard.nodes.find(n => n.id === nodeId);
      connectSourceNodeId = null;
      renderScoutCanvas(currentCanvasBoard);
      if ($("#canvasLogisticsText")) $("#canvasLogisticsText").textContent = `Connected "${fromN ? fromN.label : 'Node'}" ➔ "${toN ? toN.label : 'Node'}". Order and travel matrix updated!`;
      await api("/scout/canvas", { method: "POST", body: JSON.stringify(currentCanvasBoard) });
    }
  });

  $$("#canvasBoardArea .del-node").forEach(b => b.onclick = async (e) => {
    e.stopPropagation();
    currentCanvasBoard.nodes = currentCanvasBoard.nodes.filter(n => n.id !== b.dataset.id);
    currentCanvasBoard.connections = currentCanvasBoard.connections.filter(c => c.from_node_id !== b.dataset.id && c.to_node_id !== b.dataset.id);
    renderScoutCanvas(currentCanvasBoard);
    await api("/scout/canvas", { method: "POST", body: JSON.stringify(currentCanvasBoard) });
  });

  let dragged = null, offset = {x:0, y:0};
  $$("#canvasBoardArea .canvas-node").forEach(el => {
    el.onmousedown = (e) => {
      if (e.target.classList.contains("del-node") || e.target.classList.contains("conn-node")) return;
      dragged = el;
      const rect = el.getBoundingClientRect();
      offset.x = e.clientX - rect.left;
      offset.y = e.clientY - rect.top;
      el.style.zIndex = 100;
    };
  });
  area.onmousemove = (e) => {
    if (!dragged) return;
    const areaRect = area.getBoundingClientRect();
    let x = e.clientX - areaRect.left - offset.x + area.scrollLeft;
    let y = e.clientY - areaRect.top - offset.y + area.scrollTop;
    x = Math.max(0, Math.min(area.scrollWidth - 175, x));
    y = Math.max(0, Math.min(area.scrollHeight - 90, y));
    dragged.style.left = x + "px";
    dragged.style.top = y + "px";
    const n = currentCanvasBoard.nodes.find(node => node.id === dragged.dataset.id);
    if (n) { n.x = x; n.y = y; }
    const connsEl = area.querySelector("svg");
    if (connsEl && currentCanvasBoard) {
      let linesHtml = `<defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--accent, #6366F1)"/>
        </marker>
      </defs>`;
      currentCanvasBoard.connections.forEach(c => {
        const n1 = currentCanvasBoard.nodes.find(node => node.id === c.from_node_id);
        const n2 = currentCanvasBoard.nodes.find(node => node.id === c.to_node_id);
        if (n1 && n2) {
          const x1 = n1.x + 85, y1 = n1.y + 40, x2 = n2.x + 85, y2 = n2.y + 40;
          linesHtml += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="var(--accent, #6366F1)" stroke-width="2" stroke-dasharray="4" marker-end="url(#arrow)"/>`;
          linesHtml += `<text x="${(x1+x2)/2}" y="${(y1+y2)/2 - 8}" fill="var(--muted, #9CA3AF)" font-size="11" font-weight="600" text-anchor="middle" style="background:#000;padding:2px;border-radius:3px">${esc(c.travel_time_min || 15)} min / $${esc(c.logistics_cost_usd || 150)}</text>`;
        }
      });
      connsEl.innerHTML = linesHtml;
    }
  };
  area.onmouseup = async () => {
    if (dragged) {
      dragged.style.zIndex = 10;
      dragged = null;
      if (currentCanvasBoard) {
        renderScoutCanvas(currentCanvasBoard);
        await api("/scout/canvas", { method: "POST", body: JSON.stringify(currentCanvasBoard) });
      }
    }
  };
  area.onmouseleave = area.onmouseup;
}

$("#btnScout").onclick = async () => {
  const needEl = $("#scoutNeed");
  const need = needEl ? needEl.value.trim() : "";
  if (!need) return;

  // Auto-detect filter settings from user prompt
  const lower = need.toLowerCase();
  const regEl = $("#scoutRegion");
  if (regEl) {
    if (lower.includes("nyc") || lower.includes("new york") || lower.includes("manhattan") || lower.includes("brooklyn")) regEl.value = "New York, NY";
    else if (lower.includes("la") || lower.includes("los angeles") || lower.includes("hollywood") || lower.includes("california")) regEl.value = "Los Angeles, CA";
    else if (lower.includes("london") || lower.includes("uk")) regEl.value = "London, UK";
    else if (lower.includes("tokyo") || lower.includes("japan")) regEl.value = "Tokyo, Japan";
  }
  const budEl = $("#scoutBudget");
  if (budEl) {
    if (lower.includes("free") || lower.includes("student") || lower.includes("$0") || lower.includes("no budget")) budEl.value = "Free";
    else if (lower.includes("low") || lower.includes("under") || lower.includes("cheap") || lower.includes("indie") || lower.includes("5k")) budEl.value = "Low";
    else if (lower.includes("high") || lower.includes("luxury") || lower.includes("mansion") || lower.includes("expensive")) budEl.value = "High";
  }
  const timeEl = $("#scoutTime");
  if (timeEl) {
    if (lower.includes("night") || lower.includes("neon") || lower.includes("dark") || lower.includes("evening")) timeEl.value = "Night";
    else if (lower.includes("day") || lower.includes("sunlight") || lower.includes("morning") || lower.includes("noon")) timeEl.value = "Day";
    else if (lower.includes("magic hour") || lower.includes("sunset") || lower.includes("dusk") || lower.includes("dawn") || lower.includes("golden hour")) timeEl.value = "Magic Hour";
  }

  $("#btnScout").disabled = true;
  if ($("#scoutStatus")) $("#scoutStatus").textContent = "Scouting with Gemini 2.5 Vibe Match (auto-detected filters applied)...";
  await sse("/scout", {
    need, region: regEl ? regEl.value : "New York, NY",
    scene: $("#scoutScene").value ? +$("#scoutScene").value : null,
  }, { run_end: () => {
    if ($("#scoutStatus")) $("#scoutStatus").textContent = "Scout complete! Found locations with real Google Maps images.";
    load();
  }});
  $("#btnScout").disabled = false;
};

/* ------------------------------------------------------------------- the canon */

async function loadCanon() {
  const { proposals } = await api("/proposals");
  $("#canon").innerHTML = proposals.length ? proposals.map((p) => `
    <div class="panel pad" style="margin-bottom:9px">
      <div class="tiny faint mono">${esc(p.source_agent)} · ${esc(p.field)}</div>
      <div class="tiny" style="margin:6px 0 8px;line-height:1.5">${esc(p.rationale)}</div>
      <div style="display:flex;gap:7px">
        <button class="act primary p-ok" data-id="${p.id}" style="padding:3px 9px;font-size:11px">promote</button>
        <button class="act p-no" data-id="${p.id}" style="padding:3px 9px;font-size:11px">reject</button>
      </div>
    </div>`).join("") : `<div class="faint tiny">Nothing pending.</div>`;
  $$("#canon .p-ok").forEach((b) => b.onclick = async () => {
    await api(`/proposals/${b.dataset.id}/promote`, { method: "POST" }); load(); });
  $$("#canon .p-no").forEach((b) => b.onclick = async () => {
    await api(`/proposals/${b.dataset.id}/reject`, { method: "POST" }); loadCanon(); });
}

/* --------------------------------------------------------------------- health */

async function health() {
  try {
    const h = await (await fetch("/healthz")).json();
    $("#healthname").textContent = h.ok
      ? `${h.chunks} chunks · ${h.chunks_embedded} embedded · ${h.maps_key ? "maps on" : "no maps key"}`
      : `degraded · ${h.api_error || h.seed_error}`;
    $("#health").style.color = h.ok ? "var(--ok)" : "var(--bad)";
  } catch { $("#healthname").textContent = "backend unreachable"; }
}

/* ----------------------------------------------------------------------- boot */

(async function boot() {
  await load();
  health();
  budget();
  // Embed the chunks once, in the background. Until this lands, search is
  // lexical only, which is a visible degradation rather than a wrong answer.
  api("/bible/embed", { method: "POST" })
    .then((r) => { health(); trace("bible", `${r.embedded_total}/${r.total} chunks embedded`, "done"); })
    .catch(() => trace("bible", "embedding unavailable, search is lexical only", "viol"));
})();

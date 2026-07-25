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
  bindFrameButtons();
}

/* Every frame carries three actions: regenerate (same shot), more (variants off
 * this frame) and tinker (nudge this frame with an instruction). Rebound after
 * any repaint, exactly like the regenerate button always was. */
function bindFrameButtons() {
  $$("#board .regen").forEach((b) => b.onclick = () => renderOne(b.dataset.id));
  $$("#board .more").forEach((b) => b.onclick = () => moreLike(b.dataset.id));
  $$("#board .tink").forEach((b) => b.onclick = () => tinker(b.dataset.id));
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
        <button class="act more" data-id="${sh.id}" style="padding:3px 8px;font-size:11px">more</button>
        <button class="act tink" data-id="${sh.id}" style="padding:3px 8px;font-size:11px">tinker</button>
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
  bindFrameButtons();
}

async function budget() {
  try {
    const b = await api("/board/budget");
    $("#budget").textContent = `${b.spent}/${b.cap} images`;
    $("#budget").className = "chip mono " + (b.spent >= b.cap ? "bad" : b.spent ? "warn" : "");
  } catch {}
}

/* More frames off one chosen frame. They stream in and land as new cards; the
 * final load() re-renders the scene so they sit in order. */
async function moreLike(id) {
  const el = $(`#f-${id} .img`);
  if (el) el.insertAdjacentHTML("beforeend", `<div class="shimmer"></div>`);
  await sse(`/shots/${id}/more`, { n: 2, style: $("#bdStyle").value }, {
    shot_ready: (e) => paint(e.shot),
    run_end: () => { load(); budget(); },
  });
}

/* Tinker one frame with a plain instruction. Same shot, regenerated in place. */
async function tinker(id) {
  const instruction = prompt("Tinker this frame. What should change?");
  if (!instruction || !instruction.trim()) return;
  const el = $(`#f-${id} .img`);
  if (el) el.insertAdjacentHTML("beforeend", `<div class="shimmer"></div>`);
  await sse(`/shots/${id}/tinker`,
    { instruction: instruction.trim(), style: $("#bdStyle").value }, {
    shot_ready: (e) => paint(e.shot),
    run_end: () => { load(); budget(); },
  });
}

/* Import a screenplay (paste or PDF) straight onto the board. Raw fetch, because
 * api() sends JSON and this is multipart. */
$("#btnImport").onclick = async () => {
  const text = $("#bdScript").value.trim();
  const file = $("#bdFile").files[0];
  if (!text && !file) { trace("import", "paste a script or choose a PDF first", "viol"); return; }
  const fd = new FormData();
  if (text) fd.append("text", text);
  if (file) fd.append("file", file);
  fd.append("replace", $("#bdReplace").checked ? "true" : "false");
  $("#btnImport").disabled = true;
  $$("#itabs button")[1].click();
  try {
    const r = await fetch("/api/scenes/import", { method: "POST", body: fd });
    if (!r.ok) { trace("import", `${r.status} ${(await r.text()).slice(0, 200)}`, "err"); return; }
    const j = await r.json();
    trace("import", `${j.imported} scene(s) added, starting at ${j.first_number}`, "done");
    $("#bdScript").value = ""; $("#bdFile").value = "";
    await load();
    $("#bdScene").value = j.first_number;
    drawBoard();
  } catch (e) { trace("import", String(e), "err"); }
  finally { $("#btnImport").disabled = false; }
};

/* Director chat for the selected scene. Text only: decide coverage before paying
 * for frames, which is the same order the board itself works in. */
async function sceneChat() {
  const n = +$("#bdScene").value;
  const msg = $("#bdChatMsg").value.trim();
  if (!msg) return;
  const log = $("#bdChat");
  if (log.classList.contains("faint")) { log.classList.remove("faint"); log.innerHTML = ""; }
  log.insertAdjacentHTML("beforeend", `<div class="turn you"><b>you</b> ${esc(msg)}</div>`);
  $("#bdChatMsg").value = "";
  $("#btnChat").disabled = true;
  log.scrollTop = log.scrollHeight;
  await sse(`/scenes/${n}/chat`, { message: msg }, {
    data: (e) => {
      log.insertAdjacentHTML("beforeend",
        `<div class="turn dir"><b>director</b> ${esc(e.reply || "")}</div>`);
      log.scrollTop = log.scrollHeight;
    },
  });
  $("#btnChat").disabled = false;
}
$("#btnChat").onclick = sceneChat;
$("#bdChatMsg").addEventListener("keydown", (e) => { if (e.key === "Enter") sceneChat(); });

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

function drawLocs() {
  $("#locs").innerHTML = STORY.locations.length ? STORY.locations.map((l) => `
    <div class="panel pad">
      <div style="font-weight:500">${esc(l.name)}</div>
      <div class="tiny faint" style="margin:5px 0 8px">${esc(l.address)}</div>
      ${l.notes ? `<div class="tiny muted" style="margin-bottom:8px">${esc(l.notes)}</div>` : ""}
      <div class="tiny mono faint">${l.lat.toFixed(4)}, ${l.lng.toFixed(4)}</div>
      <div style="display:flex;gap:7px;margin-top:10px;align-items:center">
        <span class="chip ${l.shortlisted ? "ok" : ""}">${l.shortlisted ? "shortlisted" : "draft"}</span>
        <a class="chip" href="${safeUrl(l.maps_url)}" target="_blank" rel="noreferrer">maps</a>
        <button class="act short" data-id="${l.id}" data-on="${!l.shortlisted}"
          style="margin-left:auto;padding:3px 9px;font-size:11px">
          ${l.shortlisted ? "unshortlist" : "shortlist"}</button>
      </div>
    </div>`).join("") : `<div class="empty">No locations yet.</div>`;
  $$("#locs .short").forEach((b) => b.onclick = async () => {
    await api(`/locations/${b.dataset.id}`, {
      method: "PATCH", body: JSON.stringify({ shortlisted: b.dataset.on === "true" }) });
    load();
  });
}

$("#btnScout").onclick = async () => {
  const need = $("#scoutNeed").value.trim();
  if (!need) return;
  $("#btnScout").disabled = true;
  await sse("/scout", {
    need, region: $("#scoutRegion").value,
    scene: $("#scoutScene").value ? +$("#scoutScene").value : null,
  }, { run_end: () => load() });
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

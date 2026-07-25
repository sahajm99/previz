/* Bible tab · entity lists, the Context Inspector, and chunk traceability.
 *
 * Loaded after app.js and wrapped in an IIFE, so it declares nothing at global
 * scope and cannot collide with the shell while both files are being edited on
 * different branches today. It talks to the same API and reads no state from
 * app.js, which means it also cannot be broken by a change in there.
 *
 * The Context Inspector is the point of this file. Everything the product
 * generates is produced from one assembled Continuity Pack (spec §5.4), so the
 * only honest way to debug a wrong line is to look at the pack that produced it:
 * which slots were filled, how close each came to its ceiling, what was dropped
 * on overflow, and which retrieved chunk carried the claim. Each chunk id opens
 * the row it was derived from, so a line traces to a fact in one click instead of
 * a guess.
 */

(function bible() {
  "use strict";

  const q = (s, r = document) => r.querySelector(s);
  const qa = (s, r = document) => [...r.querySelectorAll(s)];
  const clean = (s) => String(s ?? "").replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  const get = async (path) => {
    const r = await fetch("/api" + path);
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 200)}`);
    return r.json();
  };

  let story = null;
  let picked = new Set();      // character ids in scope for the pack

  /* A layer decides a CSS class, so it is resolved against the two values the
   * spec defines rather than interpolated. Anything else is treated as draft,
   * which is the safe direction: unknown provenance must never render as
   * settled. */
  const layerOf = (o) => (o && o.layer === "canon") ? "canon" : "draft";

  /* --------------------------------------------------------- entity lists */

  /* Locations belong on this tab as well as on Scout. Scout is where you go to
   * find one, and the Bible is where you check what the story currently holds,
   * which is a different question and the reason a shortlisted place is canon
   * while a suggestion is not. */
  function drawLocations() {
    const box = q("#bibleLocs");
    if (!box) return;
    const locs = story.locations || [];
    // A shortlisted place is canon, an unshortlisted suggestion is not, and §5.1
    // says the two must not read the same. Scout writes both into the same list.
    box.innerHTML = locs.length ? locs.map((l) => {
      const lay = l.shortlisted ? "canon" : "draft";
      const lat = Number(l.lat) || 0, lng = Number(l.lng) || 0;
      return `
      <div class="ent ${l.shortlisted ? "is-canon" : "is-draft"}">
        <div class="top">
          <span class="nm">${clean(l.name)}</span>
          <span class="lay ${lay}">${lay}</span>
        </div>
        <div class="sub">${clean(l.address)}</div>
        ${l.notes ? `<div class="body">${clean(l.notes)}</div>` : ""}
        <div class="feet">
          <span class="chip mono">${lat.toFixed(4)}, ${lng.toFixed(4)}</span>
          <span class="chip">${Number(l.photos?.length) || 0} photos</span>
        </div>
      </div>`;
    }).join("")
      : `<div class="empty">No locations yet. Scout writes them here, and a place
           only becomes canon when it is shortlisted.</div>`;
  }

  /* --------------------------------------------------- the Context Inspector */

  function castChips() {
    const box = q("#ciCast");
    if (!box) return;
    box.innerHTML = (story.characters || []).map((c) =>
      `<button data-id="${clean(c.id)}" class="${picked.has(c.id) ? "on" : ""}">${clean(c.name)}</button>`
    ).join("");
    qa("#ciCast button", box).forEach((b) => b.onclick = () => {
      picked.has(b.dataset.id) ? picked.delete(b.dataset.id) : picked.add(b.dataset.id);
      b.classList.toggle("on");
      assemble();
    });
  }

  function fillScenes() {
    const sel = q("#ciScene");
    if (!sel) return;
    sel.innerHTML = `<option value="">no scene</option>` +
      (story.scene_index || []).map((s) =>
        `<option value="${s.number}">${s.number}. ${clean(s.slugline)}</option>`).join("");
  }

  async function assemble() {
    const out = q("#ciOut");
    if (!out) return;
    const params = new URLSearchParams();
    const query = q("#ciQuery")?.value.trim() || "";
    const scene = q("#ciScene")?.value || "";
    if (query) params.set("q", query);
    if (scene) params.set("scene", scene);
    if (picked.size) params.set("character_ids", [...picked].join(","));

    let pack;
    try {
      pack = await get(`/bible/context?${params}`);
    } catch (err) {
      out.innerHTML = `<div class="empty">Could not assemble the pack. ${clean(err.message)}</div>`;
      return;
    }

    const sizes = pack.report.slots || {};
    const budgets = pack.budgets || {};
    const dropped = pack.report.dropped || [];
    const order = ["style", "spine", "cast", "retrieved", "local", "turn"];
    const nCast = picked.size || (story.characters || []).length || 1;

    out.innerHTML = `
      <div class="ci-sum">
        <span><b>${Number(pack.report.total_chars) || 0}</b> chars assembled</span>
        <span><b>${(pack.chunks || []).length}</b> chunks retrieved</span>
        <span>${dropped.length
          ? `<b style="color:var(--bad)">dropped ${clean(dropped.join(", "))}</b>`
          : `<b>nothing dropped</b>`}</span>
      </div>
      ${order.map((k) => slotRow(k, sizes[k] || 0,
                                 k === "cast" ? (budgets.cast || 300) * nCast : budgets[k],
                                 dropped.includes(k), pack.slots[k] || "",
                                 k === "cast" ? `${budgets.cast || 300} per character` : "")).join("")}

      <div class="eyebrow" style="margin-top:20px">
        Retrieved · every claim in the pack, and where it came from
      </div>
      ${(pack.chunks || []).length
        ? pack.chunks.map(chunkRow).join("")
        : `<div class="empty">Nothing retrieved. The retrieved slot only fills when
             there is a query, so type one above to see hybrid search feeding the
             pack.</div>`}
    `;

    qa("#ciOut .sl .hd").forEach((h) => h.onclick = () => {
      const pre = h.parentElement.querySelector("pre");
      if (pre) pre.hidden = !pre.hidden;
    });
    qa("#ciOut .cid").forEach((b) => b.onclick = () => openSource(b.dataset.id));
  }

  /* One slot: how full, whether it survived overflow, and its verbatim text.
   * `style` and `cast` are marked as kept, because the drop order never touches
   * them: they are the identity, and a pack that drops identity to fit is the
   * drift the whole product is built to prevent. */
  function slotRow(name, used, budget, wasDropped, text, note) {
    const pct = budget ? Math.min(100, used / budget * 100) : 0;
    const cls = [
      "sl",
      wasDropped ? "gone" : "",
      !used ? "empty-slot" : "",
      budget && used >= budget ? "full" : "",
    ].join(" ");
    const kept = (name === "style" || name === "cast")
      ? `<span class="keep">never dropped</span>` : "";
    return `
      <div class="${cls}">
        <div class="hd">
          <span>${name}</span>
          ${kept}
          <span class="grow"></span>
          <span class="n">${used}${budget ? ` / ${budget}` : ""}${note ? ` · ${note}` : ""}${wasDropped ? " · dropped on overflow" : ""}</span>
        </div>
        <div class="bar"><i style="width:${pct.toFixed(1)}%"></i></div>
        ${text ? `<pre hidden>${clean(text)}</pre>` : ""}
      </div>`;
  }

  function chunkRow(c) {
    const lay = layerOf(c);
    return `
      <div class="ck ${lay === "canon" ? "is-canon" : "is-draft"}">
        <div class="meta">
          <span class="lay ${lay}">${lay}</span>
          <span class="cid" data-id="${clean(c.id)}" title="open the row this was derived from">${clean(c.id)}</span>
          <span class="sub" style="color:var(--ink-faint);font-size:11.5px">${clean(c.entity_type)}</span>
        </div>
        <div class="t">${clean(c.text)}</div>
      </div>`;
  }

  /* ------------------------------------------------------- the source drawer */

  function drawer() {
    let d = q("#srcDrawer");
    if (d) return d;
    d = document.createElement("aside");
    d.id = "srcDrawer";
    d.innerHTML = `<button class="x" aria-label="close">×</button><div id="srcBody"></div>`;
    document.body.append(d);
    d.querySelector(".x").onclick = () => d.classList.remove("on");
    addEventListener("keydown", (e) => { if (e.key === "Escape") d.classList.remove("on"); });
    return d;
  }

  async function openSource(cid) {
    const d = drawer();
    const body = q("#srcBody", d);
    body.innerHTML = `<div class="faint tiny">Loading ${clean(cid)}</div>`;
    d.classList.add("on");
    let c;
    try {
      c = await get(`/bible/chunks/${encodeURIComponent(cid)}`);
    } catch (err) {
      body.innerHTML = `<div class="empty">${clean(err.message)}</div>`;
      return;
    }
    const s = c.source || {};
    // Everything below lands in innerHTML, and entity names are typed by the user
    // in the Cast and Scout tabs. So every value is escaped, including the ones
    // that look like they came from us: `created_by` is an agent name chosen by
    // whatever wrote the chunk, and `layer` is only trustworthy because it is
    // checked against a literal rather than interpolated.
    const rows = [
      ["entity", clean(s.name || c.entity_type)],
      ["kind", clean(c.entity_type)],
      ["layer", `<span class="lay ${layerOf(c)}">${layerOf(c)}</span>`],
      ["written by", clean(c.created_by || "user")],
      ["source row", `<span class="mono">${clean(c.source_ref || s.ref || "·")}</span>`],
      ["retrieval", c.embedded
        ? "vector and lexical"
        : `lexical only<div class="tiny faint">No embedding yet, so this chunk is
             found by token overlap. Run the embed pass to add the vector leg.</div>`],
      s.number ? ["scene", `${s.number} · ${clean(s.status || "")}`] : null,
      s.canon_version ? ["canon version", `v${s.canon_version} · ${s.answers} answers`] : null,
      s.address ? ["address", clean(s.address)] : null,
    ].filter(Boolean);

    body.innerHTML = `
      <div class="eyebrow">Chunk ${clean(c.id)}</div>
      <div style="font-size:15px;font-weight:500;margin-bottom:2px">${clean(s.name || c.entity_type)}</div>
      <div class="tiny faint">Chunks are derived. This is the row that wrote it, so
        correcting the row is what corrects the chunk.</div>
      <div class="${c.layer === "canon" ? "is-canon" : "is-draft"}"
           style="margin-top:14px">
        <div class="t" style="font-size:13px;line-height:1.6;color:var(--ink-dim)">${clean(c.text)}</div>
      </div>
      <dl>${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
  }

  /* ------------------------------------------------------------------- boot */

  async function boot() {
    try {
      story = await get("/story");
    } catch {
      return;               // the shell reports the outage, no need to repeat it
    }
    drawLocations();
    fillScenes();
    castChips();

    const run = q("#ciRun");
    if (run) run.onclick = assemble;
    let t;
    q("#ciQuery")?.addEventListener("input", () => {
      clearTimeout(t); t = setTimeout(assemble, 320);
    });
    q("#ciScene")?.addEventListener("change", assemble);

    // Open on the pack for the scene the demo starts in, so the panel is showing
    // something real before anyone touches it.
    const first = (story.scene_index || [])[0];
    if (first && q("#ciScene")) q("#ciScene").value = first.number;
    assemble();
  }

  // Refresh after any write the shell makes, without coupling to its internals.
  const _fetch = window.fetch;
  window.fetch = async function (...args) {
    const r = await _fetch.apply(this, args);
    const url = String(args[0] || "");
    const method = (args[1]?.method || "GET").toUpperCase();
    if (r.ok && method !== "GET" && url.includes("/api/") &&
        !url.includes("/bible/context")) {
      setTimeout(async () => {
        try { story = await get("/story"); drawLocations(); castChips(); assemble(); }
        catch { /* the shell already surfaces a failure */ }
      }, 60);
    }
    return r;
  };

  if (document.readyState === "loading") {
    addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();

const $ = (sel) => document.querySelector(sel);

let allItems = [];
let providerLookup = {};
let selectMode = false;
const selected = new Set(); // keys "source/source_id"

function itemKey(it) { return `${it.source}/${it.source_id}`; }

// ----- Modal -----------------------------------------------------------------

function showModal({ title, bodyHTML, actions }) {
  return new Promise((resolve) => {
    const root = $("#modal-root");
    $("#modal-title").textContent = title;
    $("#modal-body").innerHTML = bodyHTML || "";
    const actEl = $("#modal-actions");
    actEl.innerHTML = "";
    let resolved = false;
    const close = (val) => {
      if (resolved) return;
      resolved = true;
      root.style.display = "none";
      document.removeEventListener("keydown", onKey);
      resolve(val);
    };
    for (const a of actions) {
      const btn = document.createElement("button");
      btn.textContent = a.label;
      if (a.className) btn.className = a.className;
      btn.addEventListener("click", () => close(a.value));
      actEl.appendChild(btn);
    }
    const onKey = (e) => { if (e.key === "Escape") close(null); };
    document.addEventListener("keydown", onKey);
    root.addEventListener("click", (e) => { if (e.target === root) close(null); }, { once: true });
    root.style.display = "flex";
  });
}

async function confirmDeleteSingle(item) {
  const isTv = item.kind === "tv";

  if (!isTv) {
    return showModal({
      title: `Delete "${item.title}"?`,
      bodyHTML: `<p>This will delete the movie from Radarr <strong>and its media file</strong>. This cannot be undone.</p>`,
      actions: [
        { label: "Cancel", value: null },
        { label: "Delete", value: "all", className: "danger" },
      ],
    });
  }

  const totalEp = item.total_episodes || 0;
  const watchedEp = item.view_count || 0;
  const watchedLine = totalEp
    ? `${watchedEp} of ${totalEp} episodes watched on Plex`
    : `${watchedEp} episodes watched on Plex`;

  // If we have any watched episodes, Plex must know this show — the backend
  // can re-resolve a missing rating_key at delete time and cache it back.
  const partialDisabled = !(isTv && watchedEp > 0);
  const partialHint = !watchedEp
    ? "No watched episodes on Plex."
    : "Removes the file for each watched episode. Keeps the series in Sonarr.";

  return showModal({
    title: `Delete "${item.title}"?`,
    bodyHTML: `
      <p>${escapeHtml(watchedLine)}</p>
      <label class="choice">
        <input type="radio" name="del-mode" value="all" checked />
        <strong>Delete entire series</strong>
        <span class="hint">Removes the series from Sonarr and deletes every episode file. Cannot be undone.</span>
      </label>
      <label class="choice ${partialDisabled ? "disabled" : ""}">
        <input type="radio" name="del-mode" value="watched" ${partialDisabled ? "disabled" : ""} />
        <strong>Delete only watched episodes</strong>
        <span class="hint">${escapeHtml(partialHint)}</span>
      </label>
    `,
    actions: [
      { label: "Cancel", value: null },
      { label: "Delete", value: "__choice__", className: "danger" },
    ],
  }).then((v) => {
    if (v !== "__choice__") return null;
    const picked = document.querySelector('input[name="del-mode"]:checked');
    return picked ? picked.value : null;
  });
}

async function confirmDeleteBulk(refs) {
  const tvRefs = refs.filter((r) => r.kind === "tv");
  const movieCount = refs.length - tvRefs.length;
  const eligibleTv = tvRefs.filter((r) => (r.view_count || 0) > 0).length;

  if (!tvRefs.length) {
    return showModal({
      title: `Delete ${refs.length} movie${refs.length === 1 ? "" : "s"}?`,
      bodyHTML: `<p>This will delete each movie from Radarr <strong>and its media file</strong>. Cannot be undone.</p>`,
      actions: [
        { label: "Cancel", value: null },
        { label: "Delete", value: "all", className: "danger" },
      ],
    });
  }

  const partialAvailable = eligibleTv > 0;
  const partialHint = partialAvailable
    ? `${eligibleTv} of ${tvRefs.length} TV show${tvRefs.length === 1 ? "" : "s"} have a Plex match with watched episodes; the rest will be skipped.`
    : `None of the selected TV shows have a Plex match with watched episodes.`;

  return showModal({
    title: `Delete ${refs.length} item${refs.length === 1 ? "" : "s"}?`,
    bodyHTML: `
      <p>${movieCount ? `${movieCount} movie${movieCount === 1 ? "" : "s"} and ` : ""}${tvRefs.length} TV show${tvRefs.length === 1 ? "" : "s"} selected.</p>
      <p>For the TV shows, delete:</p>
      <label class="choice">
        <input type="radio" name="bulk-tv-mode" value="all" checked />
        <strong>Entire series</strong>
        <span class="hint">Removes each series from Sonarr and deletes every episode file.</span>
      </label>
      <label class="choice ${partialAvailable ? "" : "disabled"}">
        <input type="radio" name="bulk-tv-mode" value="watched" ${partialAvailable ? "" : "disabled"} />
        <strong>Only watched episodes</strong>
        <span class="hint">${escapeHtml(partialHint)}</span>
      </label>
      <p style="margin-top:12px;">Movies are always deleted with their file.</p>
    `,
    actions: [
      { label: "Cancel", value: null },
      { label: "Delete", value: "__choice__", className: "danger" },
    ],
  }).then((v) => {
    if (v !== "__choice__") return null;
    const picked = document.querySelector('input[name="bulk-tv-mode"]:checked');
    return picked ? picked.value : "all";
  });
}

function toast(msg, kind = "") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast " + kind;
  el.style.display = "block";
  clearTimeout(el._t);
  el._t = setTimeout(() => (el.style.display = "none"), 3500);
}

function fmtBytes(n) {
  if (!n) return "";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toFixed(n >= 10 || i === 0 ? 0 : 1) + " " + u[i];
}

function providerTagClass(name) {
  const n = (name || "").toLowerCase();
  if (n.includes("netflix")) return "tag netflix";
  if (n.includes("disney")) return "tag disney";
  return "tag";
}

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(`${r.status}: ${t}`);
  }
  if (r.status === 204) return null;
  return r.json();
}

function render() {
  const provider = $("#provider-filter").value;
  const kind = $("#kind-filter").value;
  const query = $("#search").value.trim().toLowerCase();

  const container = $("#cards");
  container.innerHTML = "";

  const filtered = allItems.filter((it) => {
    if (provider && !(it.providers || []).map(String).includes(provider)) return false;
    if (kind && it.kind !== kind) return false;
    if (query && !(it.title || "").toLowerCase().includes(query)) return false;
    return true;
  });

  $("#count").textContent = `${filtered.length} item${filtered.length === 1 ? "" : "s"}`;
  $("#empty").style.display = allItems.length === 0 ? "block" : "none";

  for (const it of filtered) {
    const card = document.createElement("div");
    card.className = "card";
    card.dataset.key = itemKey(it);
    if (selected.has(itemKey(it))) card.classList.add("selected");
    if (it.ignored) card.style.opacity = "0.55";

    const poster = it.poster_url
      ? `<img class="poster" loading="lazy" src="${it.poster_url}" alt="" />`
      : `<div class="no-poster">${escapeHtml(it.title)}</div>`;

    const provTags = (it.provider_names || [])
      .map((n) => `<span class="${providerTagClass(n)}">${escapeHtml(n)}</span>`)
      .join("");
    let watchedTag = "";
    if (it.watched === 1) {
      const sub = it.kind === "tv" && it.total_episodes
        ? ` ${it.view_count}/${it.total_episodes}`
        : "";
      watchedTag = `<span class="tag watched">Watched${sub}</span>`;
    } else if (it.watched === 2) {
      const sub = it.total_episodes
        ? ` ${it.view_count}/${it.total_episodes}`
        : "";
      watchedTag = `<span class="tag inprogress">In progress${sub}</span>`;
    }
    let requesterTag = "";
    const reqs = it.requesters || [];
    if (reqs.length) {
      const label = reqs.length === 1
        ? `Requested by ${reqs[0]}`
        : `Requested by ${reqs[0]} +${reqs.length - 1}`;
      requesterTag = `<span class="tag requested" title="${escapeHtml(reqs.join(", "))}">${escapeHtml(label)}</span>`;
    }
    const tags = watchedTag + requesterTag + provTags;

    const arrLink = it.arr_url
      ? `<a class="btn" href="${it.arr_url}" target="_blank" rel="noopener">Open in ${it.source === "radarr" ? "Radarr" : "Sonarr"}</a>`
      : "";

    const ignoreBtn = it.ignored
      ? `<button data-action="unignore">Unignore</button>`
      : `<button data-action="ignore" class="warn">Ignore</button>`;

    card.innerHTML = `
      <div class="check"></div>
      <div class="select-overlay"></div>
      ${poster}
      <div class="body">
        <div class="title">${escapeHtml(it.title)}</div>
        <div class="meta">${it.year || ""} ${it.size_bytes ? "· " + fmtBytes(it.size_bytes) : ""}</div>
        <div class="providers">${tags}</div>
        <div class="actions">
          ${arrLink}
          ${ignoreBtn}
          <button data-action="delete" class="danger">Delete</button>
        </div>
      </div>
    `;

    card.querySelectorAll("button[data-action]").forEach((b) => {
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        doAction(it, b.dataset.action, card);
      });
    });

    card.querySelector(".select-overlay").addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      toggleSelected(it, card);
    });

    container.appendChild(card);
  }
  updateBulkBar();
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

async function doAction(item, action, card) {
  const key = `${item.source}/${item.source_id}`;
  try {
    if (action === "ignore") {
      await api(`/api/items/${key}/ignore`, { method: "POST" });
      item.ignored = 1;
      toast(`Ignored "${item.title}"`, "ok");
      if (!$("#show-ignored").checked) {
        card.remove();
      } else {
        render();
      }
    } else if (action === "unignore") {
      await api(`/api/items/${key}/unignore`, { method: "POST" });
      item.ignored = 0;
      toast(`Unignored "${item.title}"`, "ok");
      render();
    } else if (action === "delete") {
      const mode = await confirmDeleteSingle(item);
      if (!mode) return;
      const res = await api(`/api/items/${key}?episodes=${encodeURIComponent(mode)}`, {
        method: "DELETE",
      });
      if (mode === "watched") {
        // Series stays; update local watched state optimistically
        const removed = res.deleted_episodes || 0;
        item.view_count = Math.max(0, (item.view_count || 0) - removed);
        if (item.total_episodes && item.view_count < item.total_episodes) {
          item.watched = item.view_count > 0 ? 2 : 0;
        }
        toast(
          `Deleted ${res.deleted_files || 0} episode file${res.deleted_files === 1 ? "" : "s"} from "${item.title}"`,
          "ok"
        );
        render();
      } else {
        allItems = allItems.filter((x) => !(x.source === item.source && x.source_id === item.source_id));
        toast(`Deleted "${item.title}"`, "ok");
        render();
      }
    }
  } catch (e) {
    toast(e.message, "err");
  }
}

async function loadItems() {
  const showIgnored = $("#show-ignored").checked;
  const mode = $("#mode-filter").value || "streaming";
  const data = await api(
    `/api/items?include_ignored=${showIgnored ? "true" : "false"}&mode=${encodeURIComponent(mode)}`
  );
  allItems = data.items;
  render();
}

async function adaptModeDropdown() {
  // Update "Watched on X" labels and disable watched-related modes if no
  // watch source is configured.
  let cfg;
  try { cfg = await api("/api/config"); } catch { return; }
  const sources = [];
  if (cfg.plex_url && cfg.plex_token) sources.push("Plex");
  if (cfg.jellyfin_url && cfg.jellyfin_api_key) sources.push("Jellyfin");
  const label = sources.length ? sources.join(" / ") : "";

  const sel = $("#mode-filter");
  for (const opt of sel.options) {
    if (opt.value === "watched") {
      opt.textContent = label ? `Watched on ${label}` : "Watched (no source configured)";
      opt.disabled = !label;
    } else if (opt.value === "both") {
      opt.textContent = label
        ? `Watched on ${label} & on streaming (cleanup)`
        : "Watched & on streaming (no watch source configured)";
      opt.disabled = !label;
    }
  }
  if (sel.selectedOptions[0]?.disabled) {
    sel.value = "streaming";
    loadItems();
  }
}

async function loadProviderFilter() {
  const cfg = await api("/api/config");
  const sel = $("#provider-filter");
  sel.innerHTML = `<option value="">All selected providers</option>`;
  // Discover names from already-loaded items if available.
  const seen = new Map();
  for (const it of allItems) {
    (it.providers || []).forEach((pid, idx) => {
      const name = (it.provider_names || [])[idx] || `Provider ${pid}`;
      seen.set(String(pid), name);
    });
  }
  for (const pid of (cfg.providers || [])) {
    if (!seen.has(String(pid))) seen.set(String(pid), `Provider ${pid}`);
  }
  for (const [pid, name] of seen.entries()) {
    const o = document.createElement("option");
    o.value = pid;
    o.textContent = name;
    sel.appendChild(o);
  }
}

async function pollStatus() {
  try {
    const s = await api("/api/scan/status");
    const el = $("#status");
    if (s.running) {
      el.textContent = `scanning… ${s.processed}/${s.total || "?"} (${s.phase || ""})`;
      el.className = "status";
      $("#scan-btn").disabled = true;
    } else {
      $("#scan-btn").disabled = false;
      if (s.error) {
        el.textContent = "error: " + (s.error.split("\n")[0] || "");
        el.className = "status err";
      } else if (s.finished_at) {
        el.textContent = `last scan: ${s.finished_at}`;
        el.className = "status ok";
        if (el.dataset.lastFinish !== s.finished_at) {
          el.dataset.lastFinish = s.finished_at;
          await loadItems();
          await loadProviderFilter();
        }
      } else {
        el.textContent = "idle";
        el.className = "status";
      }
    }
  } catch (e) {
    // ignore
  }
}

function setSelectMode(on) {
  selectMode = on;
  document.body.classList.toggle("select-mode", on);
  $("#bulk-bar").style.display = on ? "flex" : "none";
  $("#select-mode-btn").textContent = on ? "Selecting…" : "Select";
  if (!on) {
    selected.clear();
    document.querySelectorAll(".card.selected").forEach((c) => c.classList.remove("selected"));
  }
  updateBulkBar();
}

function toggleSelected(it, card) {
  const k = itemKey(it);
  if (selected.has(k)) {
    selected.delete(k);
    card.classList.remove("selected");
  } else {
    selected.add(k);
    card.classList.add("selected");
  }
  updateBulkBar();
}

function updateBulkBar() {
  const n = selected.size;
  $("#bulk-count").textContent = `${n} selected`;
  for (const id of ["bulk-ignore", "bulk-delete", "bulk-clear"]) {
    $("#" + id).disabled = n === 0;
  }
}

function visibleSelectableItems() {
  // Re-derive currently visible items the same way render() does
  const provider = $("#provider-filter").value;
  const kind = $("#kind-filter").value;
  const query = $("#search").value.trim().toLowerCase();
  return allItems.filter((it) => {
    if (provider && !(it.providers || []).map(String).includes(provider)) return false;
    if (kind && it.kind !== kind) return false;
    if (query && !(it.title || "").toLowerCase().includes(query)) return false;
    return true;
  });
}

async function doBulk(action) {
  // Build richer refs by looking up each selected item locally (so the modal
  // can show TV vs movie counts and Plex-eligibility).
  const byKey = new Map(allItems.map((it) => [itemKey(it), it]));
  const refs = [...selected].map((k) => {
    const it = byKey.get(k);
    const [source, sid] = k.split("/");
    return {
      source, source_id: Number(sid),
      kind: it?.kind, view_count: it?.view_count || 0,
      total_episodes: it?.total_episodes || 0,
      plex_rating_key: it?.plex_rating_key || null,
    };
  });
  if (!refs.length) return;

  let tvMode = "all";
  if (action === "delete") {
    const choice = await confirmDeleteBulk(refs);
    if (!choice) return;
    tvMode = choice;
  }

  const btnIgnore = $("#bulk-ignore");
  const btnDelete = $("#bulk-delete");
  btnIgnore.disabled = btnDelete.disabled = true;
  $("#bulk-count").textContent = `${refs.length} ${action === "delete" ? "deleting" : "ignoring"}…`;

  try {
    const res = await api("/api/items/bulk", {
      method: "POST",
      body: JSON.stringify({
        action,
        items: refs.map((r) => ({ source: r.source, source_id: r.source_id })),
        tv_episodes_mode: tvMode,
      }),
    });

    const successKeys = new Set(
      res.results.filter((r) => r.ok).map((r) => `${r.source}/${r.source_id}`)
    );
    // For watched-only Sonarr deletes the series record stays. Don't drop those
    // from the local list — update them optimistically.
    const watchedOnlyResults = new Map(
      res.results
        .filter((r) => r.ok && r.mode === "watched")
        .map((r) => [`${r.source}/${r.source_id}`, r])
    );

    if (action === "delete") {
      allItems = allItems.filter((it) => {
        const k = itemKey(it);
        if (watchedOnlyResults.has(k)) {
          const r = watchedOnlyResults.get(k);
          const removed = r.deleted_episodes || 0;
          it.view_count = Math.max(0, (it.view_count || 0) - removed);
          if (it.total_episodes && it.view_count < it.total_episodes) {
            it.watched = it.view_count > 0 ? 2 : 0;
          }
          return true;
        }
        return !successKeys.has(k);
      });
    } else if (action === "ignore") {
      allItems.forEach((it) => { if (successKeys.has(itemKey(it))) it.ignored = 1; });
      if (!$("#show-ignored").checked) {
        allItems = allItems.filter((it) => !successKeys.has(itemKey(it)));
      }
    }

    successKeys.forEach((k) => selected.delete(k));

    const kind = res.failed === 0 ? "ok" : "err";
    toast(
      `${action}: ${res.succeeded} succeeded, ${res.failed} failed`,
      kind
    );
    if (res.failed) {
      const firstErr = res.results.find((r) => !r.ok);
      if (firstErr) toast(`First error: ${firstErr.error}`, "err");
    }
    render();
  } catch (e) {
    toast(e.message, "err");
  } finally {
    updateBulkBar();
  }
}

$("#select-mode-btn").addEventListener("click", () => setSelectMode(!selectMode));
$("#bulk-exit").addEventListener("click", () => setSelectMode(false));
$("#bulk-clear").addEventListener("click", () => {
  selected.clear();
  document.querySelectorAll(".card.selected").forEach((c) => c.classList.remove("selected"));
  updateBulkBar();
});
$("#bulk-select-all").addEventListener("click", () => {
  for (const it of visibleSelectableItems()) selected.add(itemKey(it));
  document.querySelectorAll("#cards .card").forEach((c) => {
    if (selected.has(c.dataset.key)) c.classList.add("selected");
  });
  updateBulkBar();
});
$("#bulk-ignore").addEventListener("click", () => doBulk("ignore"));
$("#bulk-delete").addEventListener("click", () => doBulk("delete"));

$("#scan-btn").addEventListener("click", async () => {
  try {
    await api("/api/scan", { method: "POST" });
    toast("Scan started", "ok");
    pollStatus();
  } catch (e) {
    toast(e.message, "err");
  }
});
$("#mode-filter").addEventListener("change", loadItems);
$("#provider-filter").addEventListener("change", render);
$("#kind-filter").addEventListener("change", render);
$("#search").addEventListener("input", render);
$("#show-ignored").addEventListener("change", loadItems);

(async () => {
  await adaptModeDropdown();
  await loadItems();
  await loadProviderFilter();
  pollStatus();
  setInterval(pollStatus, 2000);
})();

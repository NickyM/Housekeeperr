const $ = (s) => document.querySelector(s);

function toast(msg, kind = "") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast " + kind;
  el.style.display = "block";
  clearTimeout(el._t);
  el._t = setTimeout(() => (el.style.display = "none"), 3500);
}

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(`${r.status}: ${t}`);
  }
  return r.status === 204 ? null : r.json();
}

let selectedProviders = new Set();

async function loadConfig() {
  const cfg = await api("/api/config");
  $("#radarr_url").value = cfg.radarr_url || "";
  $("#radarr_api_key").value = cfg.radarr_api_key || "";
  $("#sonarr_url").value = cfg.sonarr_url || "";
  $("#sonarr_api_key").value = cfg.sonarr_api_key || "";
  $("#tmdb_api_key").value = cfg.tmdb_api_key || "";
  $("#plex_url").value = cfg.plex_url || "";
  $("#plex_token").value = cfg.plex_token || "";
  $("#jellyfin_url").value = cfg.jellyfin_url || "";
  $("#jellyfin_api_key").value = cfg.jellyfin_api_key || "";
  $("#jellyfin_user_id").value = cfg.jellyfin_user_id || "";
  $("#seerr_url").value = cfg.seerr_url || "";
  $("#seerr_api_key").value = cfg.seerr_api_key || "";
  selectedProviders = new Set((cfg.providers || []).map(Number));
  await loadRegions(cfg.region || "US");
  await loadProviders(cfg.region || "US");
}

async function loadRegions(current) {
  const sel = $("#region");
  sel.innerHTML = "";
  try {
    const regions = await api("/api/regions");
    for (const r of regions) {
      const o = document.createElement("option");
      o.value = r.code;
      o.textContent = `${r.code} — ${r.name}`;
      if (r.code === current) o.selected = true;
      sel.appendChild(o);
    }
  } catch (e) {
    // Fallback: still allow typing a region
    const opts = ["US", "GB", "DK", "DE", "FR", "ES", "IT", "NL", "SE", "NO", "FI", "JP", "CA", "AU"];
    for (const code of opts) {
      const o = document.createElement("option");
      o.value = code;
      o.textContent = code;
      if (code === current) o.selected = true;
      sel.appendChild(o);
    }
  }
}

async function loadProviders(region) {
  const grid = $("#provider-grid");
  grid.innerHTML = `<span class="status">Loading providers…</span>`;
  try {
    const data = await api(`/api/providers?region=${encodeURIComponent(region)}`);
    grid.innerHTML = "";
    for (const p of data.providers) {
      const label = document.createElement("label");
      const checked = selectedProviders.has(p.id) ? "checked" : "";
      label.innerHTML = `
        <input type="checkbox" value="${p.id}" ${checked} />
        <img src="${p.logo}" alt="" onerror="this.style.display='none'" />
        <span>${p.name}</span>
      `;
      label.querySelector("input").addEventListener("change", (e) => {
        const id = Number(e.target.value);
        if (e.target.checked) selectedProviders.add(id);
        else selectedProviders.delete(id);
      });
      grid.appendChild(label);
    }
    if (!data.providers.length) {
      grid.innerHTML = `<span class="status">No providers found for this region.</span>`;
    }
  } catch (e) {
    grid.innerHTML = `<span class="status err">${e.message}</span>`;
  }
}

$("#region").addEventListener("change", (e) => loadProviders(e.target.value));
$("#refresh-providers").addEventListener("click", () => loadProviders($("#region").value));

$("#save").addEventListener("click", async () => {
  $("#save-status").textContent = "Saving…";
  const payload = {
    radarr_url: $("#radarr_url").value.trim(),
    radarr_api_key: $("#radarr_api_key").value,
    sonarr_url: $("#sonarr_url").value.trim(),
    sonarr_api_key: $("#sonarr_api_key").value,
    tmdb_api_key: $("#tmdb_api_key").value,
    plex_url: $("#plex_url").value.trim(),
    plex_token: $("#plex_token").value,
    jellyfin_url: $("#jellyfin_url").value.trim(),
    jellyfin_api_key: $("#jellyfin_api_key").value,
    jellyfin_user_id: $("#jellyfin_user_id").value.trim(),
    seerr_url: $("#seerr_url").value.trim(),
    seerr_api_key: $("#seerr_api_key").value,
    region: $("#region").value,
    providers: [...selectedProviders],
  };
  try {
    await api("/api/config", { method: "POST", body: JSON.stringify(payload) });
    $("#save-status").textContent = "Saved.";
    $("#save-status").className = "status ok";
    toast("Settings saved", "ok");
  } catch (e) {
    $("#save-status").textContent = e.message;
    $("#save-status").className = "status err";
    toast(e.message, "err");
  }
});

$("#test").addEventListener("click", async () => {
  $("#save-status").textContent = "Testing…";
  $("#save-status").className = "status";
  const payload = {
    radarr_url: $("#radarr_url").value.trim(),
    radarr_api_key: $("#radarr_api_key").value,
    sonarr_url: $("#sonarr_url").value.trim(),
    sonarr_api_key: $("#sonarr_api_key").value,
    tmdb_api_key: $("#tmdb_api_key").value,
    plex_url: $("#plex_url").value.trim(),
    plex_token: $("#plex_token").value,
    jellyfin_url: $("#jellyfin_url").value.trim(),
    jellyfin_api_key: $("#jellyfin_api_key").value,
    seerr_url: $("#seerr_url").value.trim(),
    seerr_api_key: $("#seerr_api_key").value,
  };
  try {
    const res = await api("/api/config/test", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const lines = [];
    let anyFail = false;
    for (const k of ["radarr", "sonarr", "tmdb", "plex", "jellyfin", "seerr"]) {
      const r = res[k];
      if (r === null || r === undefined) {
        lines.push(`${k}: not configured`);
      } else if (r.ok) {
        lines.push(`${k}: OK (HTTP ${r.status})`);
      } else {
        anyFail = true;
        lines.push(`${k}: FAIL — ${r.error || "unknown error"}`);
      }
    }
    $("#save-status").innerHTML = lines.map((l) => escapeHtml(l)).join("<br>");
    $("#save-status").className = anyFail ? "status err" : "status ok";
  } catch (e) {
    $("#save-status").textContent = e.message;
    $("#save-status").className = "status err";
  }
});

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

loadConfig().catch((e) => toast(e.message, "err"));

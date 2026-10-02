/* Daily Podcasts full-screen Home Assistant sidebar panel. */

const PANEL_VERSION = "1.9.1";

class DailyPodcastsPanel extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._tab = "overview";
    // Management state
    this._rows = [];
    this._loaded = false;
    this._loading = false;
    this._saving = false;
    this._status = "";
    this._dragIndex = null;
    // Overview state
    this._overview = null;
    this._overviewLoading = false;
    this._overviewStatus = "";
    this._refreshTimer = null;
    this.attachShadow({ mode: "open" });
  }

  set hass(value) {
    this._hass = value;
    if (!this._loaded && this._tab === "management") this._load();
    this._render();
  }

  get hass() {
    return this._hass;
  }

  set panel(value) {
    this._panel = value || {};
  }

  set narrow(_value) {}
  set route(_value) {}

  connectedCallback() {
    // Home Assistant registers global keyboard shortcuts (quick bar, etc.) on
    // document. Key events from our inputs bubble out of the shadow root and
    // trigger those shortcuts while typing. Stop keyboard events that originate
    // from a text field from leaving the panel so typing never fires a hotkey.
    if (!this._keyGuard) {
      this._keyGuard = (event) => {
        const target = event.composedPath ? event.composedPath()[0] : event.target;
        const tag = target && target.tagName ? target.tagName.toLowerCase() : "";
        if (tag === "input" || tag === "textarea" || (target && target.isContentEditable)) {
          event.stopPropagation();
        }
      };
      for (const type of ["keydown", "keyup", "keypress"]) {
        this.addEventListener(type, this._keyGuard);
      }
    }
    this._render();
    if (this._hass) this._onTabEnter(this._tab);
  }

  disconnectedCallback() {
    this._stopRefresh();
    if (this._keyGuard) {
      for (const type of ["keydown", "keyup", "keypress"]) {
        this.removeEventListener(type, this._keyGuard);
      }
    }
  }

  // --- Tabs ------------------------------------------------------------
  _setTab(tab) {
    if (tab === this._tab) return;
    this._tab = tab;
    this._stopRefresh();
    this._render();
    this._onTabEnter(tab);
  }

  _onTabEnter(tab) {
    if (!this._hass) return;
    if (tab === "overview") {
      this._loadOverview();
      this._startRefresh();
    } else if (tab === "management" && !this._loaded) {
      this._load();
    }
  }

  _startRefresh() {
    this._stopRefresh();
    // Refresh now-playing/queue periodically while the Overview tab is open.
    this._refreshTimer = window.setInterval(() => {
      if (this._tab === "overview") this._loadOverview(true);
    }, 10000);
  }

  _stopRefresh() {
    if (this._refreshTimer) {
      window.clearInterval(this._refreshTimer);
      this._refreshTimer = null;
    }
  }

  // --- Overview data ---------------------------------------------------
  async _loadOverview(silent = false) {
    if (!this._hass || this._overviewLoading) return;
    this._overviewLoading = true;
    if (!silent) {
      this._overviewStatus = "";
      this._render();
    }
    try {
      const result = await this._hass.callWS({
        type: "call_service",
        domain: "daily_podcasts",
        service: "get_queue",
        service_data: {},
        return_response: true,
      });
      this._overview =
        result && result.response ? result.response : result || {};
      this._overviewStatus = "";
    } catch (error) {
      this._overviewStatus = `Could not load queue: ${error.message || error}`;
    } finally {
      this._overviewLoading = false;
      this._render();
    }
  }

  async _skipTo(position) {
    if (!this._hass) return;
    try {
      await this._hass.callService("daily_podcasts", "skip_to", { position });
      // Give Sonos a moment, then refresh.
      window.setTimeout(() => this._loadOverview(true), 1200);
    } catch (error) {
      this._overviewStatus = `Skip failed: ${error.message || error}`;
      this._render();
    }
  }

  // --- Management data -------------------------------------------------
  async _load() {
    if (!this._hass || this._loading) return;
    this._loading = true;
    this._status = "";
    this._render();
    try {
      const result = await this._hass.callWS({
        type: "call_service",
        domain: "daily_podcasts",
        service: "list_podcasts",
        service_data: {},
        return_response: true,
      });
      const response = result && result.response ? result.response : result || {};
      this._rows = (response.podcasts || []).map((podcast) => ({
        name: podcast.name || "",
        feed_url: podcast.feed_url || "",
        catchup: podcast.catchup !== false,
      }));
      this._loaded = true;
    } catch (error) {
      this._status = `Could not load podcasts: ${error.message || error}`;
    } finally {
      this._loading = false;
      this._render();
    }
  }

  _update(index, key, value) {
    this._rows[index] = { ...this._rows[index], [key]: value };
    this._render();
  }

  _add() {
    this._rows.push({ name: "", feed_url: "", catchup: true });
    this._render();
  }

  _remove(index) {
    this._rows.splice(index, 1);
    this._render();
  }

  _move(index, delta) {
    const target = index + delta;
    if (target < 0 || target >= this._rows.length) return;
    [this._rows[index], this._rows[target]] = [
      this._rows[target],
      this._rows[index],
    ];
    this._render();
  }

  _onDragStart(index, event) {
    this._dragIndex = index;
    event.dataTransfer.effectAllowed = "move";
  }

  _onDragOver(event) {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }

  _onDrop(index, event) {
    event.preventDefault();
    const from = this._dragIndex;
    this._dragIndex = null;
    if (from === null || from === index) return;
    const [moved] = this._rows.splice(from, 1);
    this._rows.splice(index, 0, moved);
    this._render();
  }

  _validUrl(url) {
    try {
      const parsed = new URL((url || "").trim());
      return parsed.protocol === "http:" || parsed.protocol === "https:";
    } catch (_error) {
      return false;
    }
  }

  async _save() {
    const cleaned = this._rows
      .map((row) => ({
        name: (row.name || "").trim(),
        feed_url: (row.feed_url || "").trim(),
        catchup: row.catchup !== false,
      }))
      .filter((row) => row.name && this._validUrl(row.feed_url));
    const dropped = this._rows.length - cleaned.length;

    this._saving = true;
    this._status = "Saving…";
    this._render();
    try {
      await this._hass.callService("daily_podcasts", "set_podcasts", {
        podcasts: cleaned,
      });
      this._rows = cleaned;
      this._status = `Saved ${cleaned.length} podcast(s).${
        dropped ? ` ${dropped} incomplete row(s) skipped.` : ""
      }`;
    } catch (error) {
      this._status = `Save failed: ${error.message || error}`;
    } finally {
      this._saving = false;
      this._render();
    }
  }

  // --- Formatting helpers ---------------------------------------------
  _fmtClock(seconds) {
    if (seconds === null || seconds === undefined || isNaN(seconds)) return "";
    const s = Math.max(0, Math.round(seconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    const pad = (n) => String(n).padStart(2, "0");
    return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
  }

  _fmtWhen(iso) {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      const now = new Date();
      const sameDay = d.toDateString() === now.toDateString();
      const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      if (sameDay) return `today at ${time}`;
      const tomorrow = new Date(now);
      tomorrow.setDate(now.getDate() + 1);
      if (d.toDateString() === tomorrow.toDateString()) return `tomorrow at ${time}`;
      return `${d.toLocaleDateString([], { weekday: "short" })} at ${time}`;
    } catch (_error) {
      return iso;
    }
  }

  _button(label, className, handler, disabled = false) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.textContent = label;
    button.disabled = disabled;
    button.addEventListener("click", handler);
    return button;
  }

  // --- Render ----------------------------------------------------------
  _render() {
    if (!this.shadowRoot) return;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; min-height:100%; background:var(--primary-background-color); color:var(--primary-text-color); }
        .page { max-width:1100px; margin:0 auto; padding:28px 32px 48px; box-sizing:border-box; }
        h1 { margin:0; font-size:32px; font-weight:500; }
        .intro { margin:8px 0 20px; color:var(--secondary-text-color); line-height:1.5; }
        .tabs { display:flex; gap:4px; border-bottom:1px solid var(--divider-color,#ddd); margin-bottom:22px; }
        .tab { border:0; background:transparent; color:var(--secondary-text-color); cursor:pointer; font:inherit; font-size:15px; padding:12px 18px; border-bottom:3px solid transparent; margin-bottom:-1px; }
        .tab.active { color:var(--primary-color); border-bottom-color:var(--primary-color); font-weight:500; }
        .toolbar { display:flex; align-items:center; gap:12px; margin-bottom:16px; }
        .spacer { flex:1; }
        .button { border:1px solid var(--primary-color); border-radius:8px; padding:10px 16px; background:transparent; color:var(--primary-color); cursor:pointer; font:inherit; }
        .button.primary { background:var(--primary-color); color:var(--text-primary-color,white); }
        .button:disabled { opacity:.5; cursor:default; }
        .list { display:flex; flex-direction:column; gap:10px; }
        .row { display:flex; align-items:center; gap:12px; padding:12px; background:var(--card-background-color); border:1px solid var(--divider-color,#ddd); border-radius:10px; box-sizing:border-box; }
        .row.dragging { opacity:.45; }
        .handle { color:var(--secondary-text-color); cursor:grab; font-size:22px; user-select:none; }
        .order { display:flex; flex-direction:column; gap:2px; }
        .fields { display:flex; flex:1; gap:10px; min-width:0; }
        input[type=text], input[type=url] { box-sizing:border-box; min-width:0; flex:1; padding:10px; border:1px solid var(--divider-color,#ccc); border-radius:7px; background:var(--primary-background-color); color:var(--primary-text-color); font:inherit; }
        .catchup { display:flex; align-items:center; gap:5px; color:var(--secondary-text-color); white-space:nowrap; font-size:13px; }
        .icon { border:0; background:transparent; color:var(--secondary-text-color); cursor:pointer; padding:3px 5px; }
        .icon:disabled { opacity:.3; cursor:default; }
        .delete { color:var(--error-color,#c62828); font-size:18px; }
        .empty { padding:32px; text-align:center; color:var(--secondary-text-color); background:var(--card-background-color); border-radius:10px; }
        .status { margin-top:14px; color:var(--secondary-text-color); }
        .hint { margin-top:18px; font-size:13px; color:var(--secondary-text-color); }
        /* Overview */
        .card { background:var(--card-background-color); border:1px solid var(--divider-color,#ddd); border-radius:12px; padding:18px 20px; margin-bottom:18px; box-sizing:border-box; }
        .np-label { font-size:13px; text-transform:uppercase; letter-spacing:.05em; color:var(--secondary-text-color); margin:0 0 8px; }
        .np-title { font-size:20px; font-weight:500; margin:0; }
        .np-sub { color:var(--secondary-text-color); margin:4px 0 0; }
        .np-state { display:inline-block; font-size:12px; text-transform:uppercase; letter-spacing:.05em; padding:2px 8px; border-radius:12px; background:var(--primary-color); color:var(--text-primary-color,white); margin-bottom:10px; }
        .np-state.paused, .np-state.idle { background:var(--secondary-text-color); }
        .progress { height:6px; border-radius:3px; background:var(--divider-color,#ddd); overflow:hidden; margin-top:12px; }
        .progress > div { height:100%; background:var(--primary-color); width:0; }
        .times { display:flex; justify-content:space-between; font-size:12px; color:var(--secondary-text-color); margin-top:5px; }
        .info { display:flex; flex-wrap:wrap; gap:18px 32px; }
        .info div { font-size:14px; }
        .info .k { color:var(--secondary-text-color); font-size:12px; text-transform:uppercase; letter-spacing:.05em; }
        .qrow { display:flex; align-items:center; gap:14px; padding:12px 14px; background:var(--card-background-color); border:1px solid var(--divider-color,#ddd); border-radius:10px; box-sizing:border-box; }
        .qrow.current { border-color:var(--primary-color); box-shadow:0 0 0 1px var(--primary-color) inset; }
        .qnum { width:26px; text-align:center; color:var(--secondary-text-color); font-variant-numeric:tabular-nums; }
        .qmeta { flex:1; min-width:0; }
        .qmeta .pod { font-size:13px; color:var(--secondary-text-color); }
        .qmeta .ttl { font-size:15px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .nowtag { font-size:11px; text-transform:uppercase; letter-spacing:.05em; color:var(--primary-color); font-weight:600; }
        @media (max-width:700px) { .page { padding:20px 16px 36px; } .fields { flex-direction:column; } .catchup { font-size:12px; } }
      </style>
    `;

    const page = document.createElement("main");
    page.className = "page";
    const heading = document.createElement("h1");
    heading.textContent = "Daily Podcasts";
    page.appendChild(heading);

    const tabs = document.createElement("div");
    tabs.className = "tabs";
    const mkTab = (id, label) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = `tab${this._tab === id ? " active" : ""}`;
      b.textContent = label;
      b.addEventListener("click", () => this._setTab(id));
      return b;
    };
    tabs.append(mkTab("overview", "Overview"), mkTab("management", "Management"));
    page.appendChild(tabs);

    if (this._tab === "overview") {
      this._renderOverview(page);
    } else {
      this._renderManagement(page);
    }

    this.shadowRoot.appendChild(page);
  }

  _renderOverview(page) {
    const ov = this._overview;
    if (!ov && this._overviewLoading) {
      const l = document.createElement("div"); l.className = "empty"; l.textContent = "Loading…"; page.appendChild(l); return;
    }
    if (!ov) {
      const l = document.createElement("div"); l.className = "empty";
      l.textContent = this._overviewStatus || "No data yet.";
      page.appendChild(l); return;
    }

    // Now playing card
    const np = ov.now_playing || {};
    const npCard = document.createElement("div");
    npCard.className = "card";
    const label = document.createElement("p"); label.className = "np-label"; label.textContent = "Now playing"; npCard.appendChild(label);
    if (np.state) {
      const st = document.createElement("span");
      st.className = `np-state ${np.state}`;
      st.textContent = np.state;
      npCard.appendChild(st);
    }
    if (np.title) {
      const title = document.createElement("p"); title.className = "np-title"; title.textContent = np.title; npCard.appendChild(title);
      if (np.podcast) { const sub = document.createElement("p"); sub.className = "np-sub"; sub.textContent = np.podcast; npCard.appendChild(sub); }
      // progress
      const dur = Number(np.duration);
      let pos = Number(np.position);
      if (np.position_updated_at && (np.state === "playing")) {
        const elapsed = (Date.now() - new Date(np.position_updated_at).getTime()) / 1000;
        if (!isNaN(elapsed) && elapsed > 0) pos = pos + elapsed;
      }
      if (dur > 0 && !isNaN(pos)) {
        const bar = document.createElement("div"); bar.className = "progress";
        const fill = document.createElement("div"); fill.style.width = `${Math.min(100, (pos / dur) * 100)}%`; bar.appendChild(fill);
        npCard.appendChild(bar);
        const times = document.createElement("div"); times.className = "times";
        const a = document.createElement("span"); a.textContent = this._fmtClock(pos);
        const b = document.createElement("span"); b.textContent = this._fmtClock(dur);
        times.append(a, b); npCard.appendChild(times);
      }
    } else {
      const sub = document.createElement("p"); sub.className = "np-sub"; sub.textContent = "Nothing playing."; npCard.appendChild(sub);
    }
    page.appendChild(npCard);

    // Info card: next prepare, auto on/off, podcast count
    const infoCard = document.createElement("div");
    infoCard.className = "card info";
    const addInfo = (k, v) => {
      const d = document.createElement("div");
      const kk = document.createElement("div"); kk.className = "k"; kk.textContent = k;
      const vv = document.createElement("div"); vv.textContent = v;
      d.append(kk, vv); infoCard.appendChild(d);
    };
    addInfo("Automatic prepare", ov.enabled ? "On" : "Off");
    addInfo("Next prepare", ov.enabled ? (this._fmtWhen(ov.next_prepare) || "—") : "—");
    addInfo("Prepare time", ov.at || "—");
    addInfo("Podcasts", String(ov.podcast_count ?? "—"));
    if (ov.queue) addInfo("Queue length", String(ov.queue.length));
    page.appendChild(infoCard);

    // Queue list
    const toolbar = document.createElement("div"); toolbar.className = "toolbar";
    const h = document.createElement("div"); h.style.fontWeight = "500"; h.textContent = "Queue";
    toolbar.append(h, (() => { const s = document.createElement("span"); s.className = "spacer"; return s; })(),
      this._button("Refresh", "button", () => this._loadOverview()));
    page.appendChild(toolbar);

    const queue = ov.queue || [];
    const list = document.createElement("section"); list.className = "list";
    if (!queue.length) {
      const empty = document.createElement("div"); empty.className = "empty";
      empty.textContent = "The queue is empty. Press the Play Daily Podcasts button to build today's queue.";
      list.appendChild(empty);
    } else {
      queue.forEach((item) => list.appendChild(this._renderQueueRow(item, ov.can_skip)));
    }
    page.appendChild(list);

    if (!ov.can_skip) {
      const hint = document.createElement("div"); hint.className = "hint";
      hint.textContent = "Skip to Podcast requires the Sonos integration (sonos.play_queue). This player does not support it.";
      page.appendChild(hint);
    }
    if (this._overviewStatus) {
      const status = document.createElement("div"); status.className = "status"; status.textContent = this._overviewStatus; page.appendChild(status);
    }
  }

  _renderQueueRow(item, canSkip) {
    const el = document.createElement("div");
    el.className = `qrow${item.current ? " current" : ""}`;
    const num = document.createElement("div"); num.className = "qnum"; num.textContent = String((item.position ?? 0) + 1); el.appendChild(num);
    const meta = document.createElement("div"); meta.className = "qmeta";
    if (item.podcast) { const pod = document.createElement("div"); pod.className = "pod"; pod.textContent = item.podcast; meta.appendChild(pod); }
    const ttl = document.createElement("div"); ttl.className = "ttl"; ttl.textContent = item.title || item.media_content_id || "(unknown)"; ttl.title = ttl.textContent; meta.appendChild(ttl);
    el.appendChild(meta);
    if (item.current) {
      const tag = document.createElement("span"); tag.className = "nowtag"; tag.textContent = "Playing"; el.appendChild(tag);
    } else {
      const skip = this._button("Skip to Podcast", "button", () => this._skipTo(item.position), !canSkip);
      if (!canSkip) skip.title = "Requires the Sonos integration";
      el.appendChild(skip);
    }
    return el;
  }

  _renderManagement(page) {
    const intro = document.createElement("p");
    intro.className = "intro";
    intro.textContent = "Manage the ordered podcast queue. Drag rows to reorder, edit details, then save your changes.";
    page.appendChild(intro);

    const toolbar = document.createElement("div");
    toolbar.className = "toolbar";
    toolbar.append(
      this._button("Add podcast", "button", () => this._add()),
      (() => { const s=document.createElement("span"); s.className="spacer"; return s; })(),
      this._button("Save", "button primary", () => this._save(), this._saving)
    );
    page.appendChild(toolbar);

    const list = document.createElement("section");
    list.className = "list";
    if (this._loading) {
      const loading = document.createElement("div"); loading.className="empty"; loading.textContent="Loading podcasts…"; list.appendChild(loading);
    } else if (!this._rows.length) {
      const empty = document.createElement("div"); empty.className="empty"; empty.textContent="No podcasts configured. Click Add podcast to begin."; list.appendChild(empty);
    } else {
      this._rows.forEach((row, index) => list.appendChild(this._renderRow(row, index)));
    }
    page.appendChild(list);

    if (this._status) { const status=document.createElement("div"); status.className="status"; status.textContent=this._status; page.appendChild(status); }
    const hint=document.createElement("div"); hint.className="hint"; hint.textContent="Catch-up includes episodes since that podcast's last successful prepare. Turn it off for today-only behaviour."; page.appendChild(hint);
  }

  _renderRow(row, index) {
    const el=document.createElement("div"); el.className="row"; el.draggable=true;
    el.addEventListener("dragstart", (event)=>{ el.classList.add("dragging"); this._onDragStart(index,event); });
    el.addEventListener("dragend", ()=>el.classList.remove("dragging"));
    el.addEventListener("dragover", (event)=>this._onDragOver(event));
    el.addEventListener("drop", (event)=>this._onDrop(index,event));
    const handle=document.createElement("div"); handle.className="handle"; handle.title="Drag to reorder"; handle.textContent="⠿";
    const order=document.createElement("div"); order.className="order";
    order.append(this._button("▲","icon",()=>this._move(index,-1),index===0), this._button("▼","icon",()=>this._move(index,1),index===this._rows.length-1));
    const fields=document.createElement("div"); fields.className="fields";
    const name=document.createElement("input"); name.type="text"; name.value=row.name||""; name.placeholder="Podcast name"; name.addEventListener("input",(e)=>this._update(index,"name",e.target.value));
    const url=document.createElement("input"); url.type="url"; url.value=row.feed_url||""; url.placeholder="https://feed.url/rss"; url.addEventListener("input",(e)=>this._update(index,"feed_url",e.target.value));
    fields.append(name,url);
    const label=document.createElement("label"); label.className="catchup"; const check=document.createElement("input"); check.type="checkbox"; check.checked=row.catchup!==false; check.addEventListener("change",(e)=>this._update(index,"catchup",e.target.checked)); label.append(check,document.createTextNode("Catch-up"));
    const remove=this._button("✕","icon delete",()=>this._remove(index)); remove.title="Remove podcast";
    el.append(handle,order,fields,label,remove); return el;
  }
}

if (!customElements.get("daily-podcasts-panel")) customElements.define("daily-podcasts-panel", DailyPodcastsPanel);
console.info(`daily-podcasts-panel ${PANEL_VERSION} loaded`);

/*
 * Daily Podcasts Card — single-screen management for the Daily Podcast Queue.
 *
 * This file intentionally uses only standard browser APIs. It does not import
 * Lit or any CDN dependency, so it works in self-hosted Home Assistant
 * installations without external network access.
 */

const CARD_VERSION = "1.7.1";

class DailyPodcastsCard extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._config = {};
    this._rows = [];
    this._loading = false;
    this._saving = false;
    this._status = "";
    this._dragIndex = null;
    this.attachShadow({ mode: "open" });
  }

  set hass(value) {
    this._hass = value;
    if (!this._loaded) this._load();
    this._render();
  }

  get hass() {
    return this._hass;
  }

  setConfig(config) {
    this._config = config || {};
  }

  getCardSize() {
    return 6;
  }

  static getStubConfig() {
    return {};
  }

  connectedCallback() {
    this._render();
    if (this._hass && !this._loaded) this._load();
  }

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
        dropped ? ` ${dropped} incomplete row(s) were skipped.` : ""
      }`;
    } catch (error) {
      this._status = `Save failed: ${error.message || error}`;
    } finally {
      this._saving = false;
      this._render();
    }
  }

  _button(label, className, handler, disabled = false) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.className = className;
    button.disabled = disabled;
    button.addEventListener("click", handler);
    return button;
  }

  _render() {
    if (!this.shadowRoot) return;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { overflow: hidden; }
        .body { padding: 8px 16px 0; display: flex; flex-direction: column; gap: 8px; }
        .muted { color: var(--secondary-text-color); padding: 8px 0 12px; }
        .row { display: flex; align-items: center; gap: 8px; padding: 8px;
          border: 1px solid var(--divider-color, #e0e0e0); border-radius: 8px;
          background: var(--card-background-color); }
        .handle { cursor: grab; font-size: 18px; color: var(--secondary-text-color);
          user-select: none; }
        .order { display: flex; flex-direction: column; }
        .fields { display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 0; }
        input.name, input.url { width: 100%; box-sizing: border-box; padding: 6px 8px;
          border: 1px solid var(--divider-color, #ccc); border-radius: 6px;
          background: var(--primary-background-color); color: var(--primary-text-color); font: inherit; }
        .catchup { display: flex; align-items: center; gap: 4px; font-size: 12px;
          color: var(--secondary-text-color); white-space: nowrap; }
        button.icon { background: none; border: none; cursor: pointer;
          color: var(--secondary-text-color); font-size: 12px; line-height: 1; padding: 2px 4px; }
        button.icon:disabled { opacity: .3; cursor: default; }
        button.delete { color: var(--error-color, #c62828); font-size: 14px; }
        .actions { display: flex; align-items: center; padding: 12px 16px; gap: 8px; }
        .spacer { flex: 1; }
        .action { border: 1px solid var(--primary-color); border-radius: 6px; padding: 8px 14px;
          background: transparent; color: var(--primary-color); cursor: pointer; font: inherit; }
        .action.primary { background: var(--primary-color); color: var(--text-primary-color, white); }
        .action:disabled { opacity: .5; cursor: default; }
        .status { padding: 0 16px 12px; color: var(--secondary-text-color); font-size: 13px; }
      </style>
    `;

    const card = document.createElement("ha-card");
    card.header = "Daily Podcasts";
    const body = document.createElement("div");
    body.className = "body";
    card.appendChild(body);

    if (this._loading) {
      const loading = document.createElement("div");
      loading.className = "muted";
      loading.textContent = "Loading…";
      body.appendChild(loading);
    } else if (!this._rows.length) {
      const empty = document.createElement("div");
      empty.className = "muted";
      empty.textContent = "No podcasts yet. Add one below.";
      body.appendChild(empty);
    } else {
      this._rows.forEach((row, index) => body.appendChild(this._renderRow(row, index)));
    }

    const actions = document.createElement("div");
    actions.className = "actions";
    const add = this._button("Add podcast", "action", () => this._add());
    const spacer = document.createElement("span");
    spacer.className = "spacer";
    const save = this._button("Save", "action primary", () => this._save(), this._saving);
    actions.append(add, spacer, save);
    card.appendChild(actions);

    if (this._status) {
      const status = document.createElement("div");
      status.className = "status";
      status.textContent = this._status;
      card.appendChild(status);
    }
    this.shadowRoot.appendChild(card);
  }

  _renderRow(row, index) {
    const element = document.createElement("div");
    element.className = "row";
    element.draggable = true;
    element.addEventListener("dragstart", (event) => this._onDragStart(index, event));
    element.addEventListener("dragover", (event) => this._onDragOver(event));
    element.addEventListener("drop", (event) => this._onDrop(index, event));

    const handle = document.createElement("div");
    handle.className = "handle";
    handle.title = "Drag to reorder";
    handle.textContent = "⠿";

    const order = document.createElement("div");
    order.className = "order";
    order.append(
      this._button("▲", "icon", () => this._move(index, -1), index === 0),
      this._button("▼", "icon", () => this._move(index, 1), index === this._rows.length - 1)
    );

    const fields = document.createElement("div");
    fields.className = "fields";
    const name = document.createElement("input");
    name.className = "name";
    name.type = "text";
    name.placeholder = "Name";
    name.value = row.name || "";
    name.addEventListener("input", (event) => this._update(index, "name", event.target.value));
    const url = document.createElement("input");
    url.className = "url";
    url.type = "url";
    url.placeholder = "https://feed.url/rss";
    url.value = row.feed_url || "";
    url.addEventListener("input", (event) => this._update(index, "feed_url", event.target.value));
    fields.append(name, url);

    const catchup = document.createElement("label");
    catchup.className = "catchup";
    catchup.title = "Catch up missed episodes";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = row.catchup !== false;
    checkbox.addEventListener("change", (event) => this._update(index, "catchup", event.target.checked));
    catchup.append(checkbox, document.createTextNode("Catch-up"));

    const remove = this._button("✕", "icon delete", () => this._remove(index));
    remove.title = "Remove";
    element.append(handle, order, fields, catchup, remove);
    return element;
  }
}

if (!customElements.get("daily-podcasts-card")) {
  customElements.define("daily-podcasts-card", DailyPodcastsCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "daily-podcasts-card")) {
  window.customCards.push({
    type: "daily-podcasts-card",
    name: "Daily Podcasts",
    preview: false,
    description: "Manage the Daily Podcast Queue in one screen.",
  });
}

console.info(`daily-podcasts-card ${CARD_VERSION} loaded`);

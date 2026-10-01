/*
 * Daily Podcasts Card — single-screen management for the Daily Podcast Queue
 * integration. Add, remove, edit and drag-to-reorder podcasts, then Save.
 *
 * No build step: uses the Lit bundled with Home Assistant's frontend.
 */

const CARD_VERSION = "1.7.0";

const LitBase =
  // Reuse the Lit that HA already loads via any existing element.
  (customElements.get("ha-card") &&
    Object.getPrototypeOf(customElements.get("ha-card"))) ||
  (customElements.get("hui-view") &&
    Object.getPrototypeOf(customElements.get("hui-view")));

// Fallback: pull LitElement/html/css from the CDN only if HA's isn't found.
let _litPromise;
async function getLit() {
  if (LitBase) {
    // Derive html/css from the module HA uses.
    const mod = await import("https://unpkg.com/lit@3?module").catch(() => null);
    if (mod) return mod;
  }
  if (!_litPromise) {
    _litPromise = import("https://unpkg.com/lit@3?module");
  }
  return _litPromise;
}

(async () => {
  const { LitElement, html, css } = await getLit();

  class DailyPodcastsCard extends LitElement {
    static get properties() {
      return {
        hass: { attribute: false },
        _config: { state: true },
        _rows: { state: true },
        _loading: { state: true },
        _saving: { state: true },
        _status: { state: true },
        _dragIndex: { state: true },
      };
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
      super.connectedCallback();
      this._rows = this._rows || [];
      this._load();
    }

    async _load() {
      if (!this.hass) return;
      this._loading = true;
      this._status = "";
      try {
        const res = await this.hass.callService(
          "daily_podcasts",
          "list_podcasts",
          {},
          undefined,
          true,
          true
        );
        const data = (res && res.response) || {};
        const podcasts = (data.podcasts || []).map((p) => ({
          name: p.name || "",
          feed_url: p.feed_url || "",
          catchup: p.catchup !== false,
        }));
        this._rows = podcasts;
      } catch (err) {
        this._status = `Could not load podcasts: ${err}`;
      } finally {
        this._loading = false;
      }
    }

    _update(i, key, value) {
      const rows = this._rows.slice();
      rows[i] = { ...rows[i], [key]: value };
      this._rows = rows;
    }

    _add() {
      this._rows = [
        ...this._rows,
        { name: "", feed_url: "", catchup: true },
      ];
    }

    _remove(i) {
      const rows = this._rows.slice();
      rows.splice(i, 1);
      this._rows = rows;
    }

    _move(i, delta) {
      const j = i + delta;
      if (j < 0 || j >= this._rows.length) return;
      const rows = this._rows.slice();
      [rows[i], rows[j]] = [rows[j], rows[i]];
      this._rows = rows;
    }

    // --- drag reorder ---
    _onDragStart(i, ev) {
      this._dragIndex = i;
      ev.dataTransfer.effectAllowed = "move";
    }
    _onDragOver(i, ev) {
      ev.preventDefault();
      ev.dataTransfer.dropEffect = "move";
    }
    _onDrop(i, ev) {
      ev.preventDefault();
      const from = this._dragIndex;
      if (from === undefined || from === i) return;
      const rows = this._rows.slice();
      const [moved] = rows.splice(from, 1);
      rows.splice(i, 0, moved);
      this._rows = rows;
      this._dragIndex = undefined;
    }

    _validUrl(u) {
      return /^https?:\/\//i.test((u || "").trim());
    }

    async _save() {
      // Keep only rows with a name and a valid URL.
      const cleaned = this._rows
        .map((r) => ({
          name: (r.name || "").trim(),
          feed_url: (r.feed_url || "").trim(),
          catchup: r.catchup !== false,
        }))
        .filter((r) => r.name && this._validUrl(r.feed_url));

      const dropped = this._rows.length - cleaned.length;
      this._saving = true;
      this._status = "";
      try {
        await this.hass.callService("daily_podcasts", "set_podcasts", {
          podcasts: cleaned,
        });
        this._status =
          `Saved ${cleaned.length} podcast(s).` +
          (dropped ? ` ${dropped} row(s) skipped (missing name or valid URL).` : "");
        // Reload to reflect the stored (validated) list.
        await this._load();
      } catch (err) {
        this._status = `Save failed: ${err}`;
      } finally {
        this._saving = false;
      }
    }

    render() {
      if (!this.hass) return html``;
      const rows = this._rows || [];
      return html`
        <ha-card header="Daily Podcasts">
          <div class="body">
            ${this._loading
              ? html`<div class="muted">Loading…</div>`
              : rows.length === 0
              ? html`<div class="muted">
                  No podcasts yet. Add one below.
                </div>`
              : rows.map((r, i) => this._row(r, i))}
          </div>
          <div class="actions">
            <mwc-button @click=${this._add}>Add podcast</mwc-button>
            <span class="spacer"></span>
            <mwc-button raised ?disabled=${this._saving} @click=${this._save}>
              ${this._saving ? "Saving…" : "Save"}
            </mwc-button>
          </div>
          ${this._status
            ? html`<div class="status">${this._status}</div>`
            : ""}
        </ha-card>
      `;
    }

    _row(r, i) {
      return html`
        <div
          class="row"
          draggable="true"
          @dragstart=${(e) => this._onDragStart(i, e)}
          @dragover=${(e) => this._onDragOver(i, e)}
          @drop=${(e) => this._onDrop(i, e)}
        >
          <div class="handle" title="Drag to reorder">⠿</div>
          <div class="order">
            <button
              class="icon"
              title="Move up"
              ?disabled=${i === 0}
              @click=${() => this._move(i, -1)}
            >
              ▲
            </button>
            <button
              class="icon"
              title="Move down"
              ?disabled=${i === this._rows.length - 1}
              @click=${() => this._move(i, 1)}
            >
              ▼
            </button>
          </div>
          <div class="fields">
            <input
              class="name"
              type="text"
              placeholder="Name"
              .value=${r.name || ""}
              @input=${(e) => this._update(i, "name", e.target.value)}
            />
            <input
              class="url"
              type="url"
              placeholder="https://feed.url/rss"
              .value=${r.feed_url || ""}
              @input=${(e) => this._update(i, "feed_url", e.target.value)}
            />
          </div>
          <label class="catchup" title="Catch up missed episodes">
            <input
              type="checkbox"
              .checked=${r.catchup !== false}
              @change=${(e) => this._update(i, "catchup", e.target.checked)}
            />
            Catch-up
          </label>
          <button
            class="icon delete"
            title="Remove"
            @click=${() => this._remove(i)}
          >
            ✕
          </button>
        </div>
      `;
    }

    static get styles() {
      return css`
        .body {
          padding: 8px 16px 0;
          display: flex;
          flex-direction: column;
          gap: 8px;
        }
        .muted {
          color: var(--secondary-text-color);
          padding: 8px 0 12px;
        }
        .row {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 8px;
          border: 1px solid var(--divider-color, #e0e0e0);
          border-radius: 8px;
          background: var(--card-background-color);
        }
        .handle {
          cursor: grab;
          font-size: 18px;
          color: var(--secondary-text-color);
          user-select: none;
        }
        .order {
          display: flex;
          flex-direction: column;
        }
        .fields {
          display: flex;
          flex-direction: column;
          gap: 4px;
          flex: 1;
          min-width: 0;
        }
        input.name,
        input.url {
          width: 100%;
          box-sizing: border-box;
          padding: 6px 8px;
          border: 1px solid var(--divider-color, #ccc);
          border-radius: 6px;
          background: var(--primary-background-color);
          color: var(--primary-text-color);
          font: inherit;
        }
        .catchup {
          display: flex;
          align-items: center;
          gap: 4px;
          font-size: 12px;
          color: var(--secondary-text-color);
          white-space: nowrap;
        }
        button.icon {
          background: none;
          border: none;
          cursor: pointer;
          color: var(--secondary-text-color);
          font-size: 12px;
          line-height: 1;
          padding: 2px 4px;
        }
        button.icon:disabled {
          opacity: 0.3;
          cursor: default;
        }
        button.delete {
          color: var(--error-color, #c62828);
          font-size: 14px;
        }
        .actions {
          display: flex;
          align-items: center;
          padding: 12px 16px;
        }
        .spacer {
          flex: 1;
        }
        .status {
          padding: 0 16px 12px;
          color: var(--secondary-text-color);
          font-size: 13px;
        }
      `;
    }
  }

  if (!customElements.get("daily-podcasts-card")) {
    customElements.define("daily-podcasts-card", DailyPodcastsCard);
  }

  window.customCards = window.customCards || [];
  if (!window.customCards.some((c) => c.type === "daily-podcasts-card")) {
    window.customCards.push({
      type: "daily-podcasts-card",
      name: "Daily Podcasts",
      preview: false,
      description:
        "Manage your Daily Podcast Queue: add, remove, edit and reorder podcasts.",
    });
  }

  // eslint-disable-next-line no-console
  console.info(`%c daily-podcasts-card ${CARD_VERSION} `, "color:#fff;background:#3f51b5");
})();

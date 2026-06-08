export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function formatJson(value) {
  return JSON.stringify(value ?? {}, null, 2);
}

export function parseJsonEditor(text, fallback = {}) {
  const selected = String(text || "").trim();
  if (!selected) {
    return fallback;
  }
  try {
    return JSON.parse(selected);
  } catch (error) {
    throw new Error(`Invalid JSON: ${error.message}`);
  }
}

export function renderJson(value) {
  return `<pre class="json-output">${escapeHtml(formatJson(value))}</pre>`;
}

export function renderEmptyState(title, body = "") {
  return `
    <div class="empty-state">
      <strong>${escapeHtml(title)}</strong>
      ${body ? `<p>${escapeHtml(body)}</p>` : ""}
    </div>
  `;
}

export function renderRawJsonSection(value, label = "View raw JSON / 查看原始 JSON", open = false) {
  if (value === undefined || value === null) {
    return "";
  }
  return `
    <details class="raw-json-collapsible"${open ? " open" : ""}>
      <summary>${escapeHtml(label)}</summary>
      ${renderJson(value)}
    </details>
  `;
}

export function renderSummaryPanel({
  title,
  subtitle = "",
  badges = [],
  fields = [],
  actions = "",
  raw = null,
  rawLabel = "View raw JSON / 查看原始 JSON",
} = {}) {
  const badgeHtml = badges.filter(Boolean).map((badge) => {
    const tone = badge.tone || "neutral";
    return `<span class="badge-status badge-status-${escapeHtml(tone)}">${escapeHtml(badge.label)}</span>`;
  }).join("");
  const fieldHtml = fields.filter(Boolean).map((field) => {
    const value = field.html || escapeHtml(field.value ?? "N/A");
    return `
      <div class="summary-field">
        <span>${escapeHtml(field.label)}</span>
        <strong>${value}</strong>
        ${field.hint ? `<small>${escapeHtml(field.hint)}</small>` : ""}
      </div>
    `;
  }).join("");
  return `
    <section class="summary-panel">
      <div class="summary-panel-header">
        <div>
          <h3>${escapeHtml(title || "Summary")}</h3>
          ${subtitle ? `<p class="helper-text">${escapeHtml(subtitle)}</p>` : ""}
        </div>
        ${badgeHtml ? `<div class="summary-badges">${badgeHtml}</div>` : ""}
      </div>
      ${actions ? `<div class="summary-actions">${actions}</div>` : ""}
      ${fieldHtml ? `<div class="summary-grid">${fieldHtml}</div>` : renderEmptyState("No details yet", "Select a row to inspect it.")}
      ${renderRawJsonSection(raw, rawLabel)}
    </section>
  `;
}

export function renderTable(columns, rows, emptyText = "No records", options = {}) {
  if (!rows || rows.length === 0) {
    return `<div class="empty">${escapeHtml(emptyText)}</div>`;
  }
  const rowIdFor = typeof options.rowId === "function"
    ? options.rowId
    : (row) => row.id || row.task_id || row.job_id || row.schedule_id || row.profile_id || row.export_id || "";
  const head = columns.map((column) => `<th>${escapeHtml(column.label)}</th>`).join("");
  const body = rows.map((row) => {
    const cells = columns.map((column) => {
      const raw = typeof column.value === "function" ? column.value(row) : row[column.value || column.key];
      const content = typeof column.html === "function" ? column.html(row) : escapeHtml(raw);
      return `<td>${content}</td>`;
    }).join("");
    const id = options.selectable === false ? "" : rowIdFor(row);
    const classes = [];
    if (id && options.selectedId && id === options.selectedId) {
      classes.push("selected-row");
    }
    const attrs = [
      id ? `data-row-id="${escapeHtml(id)}"` : "",
      classes.length ? `class="${classes.join(" ")}"` : "",
    ].filter(Boolean).join(" ");
    return `<tr${attrs ? ` ${attrs}` : ""}>${cells}</tr>`;
  }).join("");
  return `<table class="data-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

export function renderToolbar(buttons = []) {
  return `<div class="toolbar">${buttons.map((button) => {
    const variant = button.primary ? "primary" : "secondary";
    return `<button type="button" class="${variant}" data-action="${escapeHtml(button.action)}">${escapeHtml(button.label)}</button>`;
  }).join("")}</div>`;
}

export function renderPager(name, page, labels = {}) {
  const previousLabel = labels.previous || "Previous";
  const nextLabel = labels.next || "Next";
  const offsetLabel = labels.offset || "Offset";
  const totalLabel = labels.total || "Total";
  const total = page?.total ?? 0;
  const limit = page?.limit ?? 20;
  const offset = page?.offset ?? 0;
  const previous = Math.max(0, offset - limit);
  const next = offset + limit;
  const disabledPrevious = offset <= 0 ? "disabled" : "";
  const disabledNext = !page?.has_more ? "disabled" : "";
  return `
    <div class="pager" data-pager="${escapeHtml(name)}">
      <button type="button" data-offset="${previous}" ${disabledPrevious}>${escapeHtml(previousLabel)}</button>
      <span>${escapeHtml(offsetLabel)} ${offset} / ${escapeHtml(totalLabel)} ${total}</span>
      <button type="button" data-offset="${next}" ${disabledNext}>${escapeHtml(nextLabel)}</button>
    </div>
  `;
}

export function setStatus(target, message, tone = "info") {
  if (!target) {
    return;
  }
  target.textContent = message;
  target.dataset.tone = tone;
  target.hidden = !message;
}

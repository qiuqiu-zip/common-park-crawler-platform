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

export function renderTable(columns, rows, emptyText = "No records") {
  if (!rows || rows.length === 0) {
    return `<div class="empty">${escapeHtml(emptyText)}</div>`;
  }
  const head = columns.map((column) => `<th>${escapeHtml(column.label)}</th>`).join("");
  const body = rows.map((row) => {
    const cells = columns.map((column) => {
      const raw = typeof column.value === "function" ? column.value(row) : row[column.value || column.key];
      return `<td>${escapeHtml(raw)}</td>`;
    }).join("");
    const id = row.id || row.task_id || row.job_id || row.schedule_id || row.profile_id || row.export_id || "";
    return `<tr data-row-id="${escapeHtml(id)}">${cells}</tr>`;
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

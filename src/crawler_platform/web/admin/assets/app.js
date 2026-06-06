import { apiDelete, apiGet, apiPost, apiPut, ApiClientError } from "./api.js?v=20260606c";
import { escapeHtml, formatJson, parseJsonEditor, renderJson, renderPager, renderTable, renderToolbar, setStatus } from "./components.js?v=20260606c";

const view = document.getElementById("view");
const toast = document.getElementById("toast");
const loadingBar = document.getElementById("loadingBar");
const adminTitle = document.getElementById("adminTitle");
const runtimeSummary = document.getElementById("runtimeSummary");
const languageToggleButton = document.getElementById("languageToggleButton");
const refreshButton = document.getElementById("refreshButton");
const quickstartExampleIds = new Set(["local-api-json", "local-html-list", "pagination-page", "detail-follow"]);
const localeStorageKey = "crawler-platform-admin-locale";

const navLabels = {
  dashboard: { en: "Dashboard", zh: "仪表盘" },
  examples: { en: "Examples", zh: "示例" },
  spiders: { en: "Spiders", zh: "爬虫" },
  tasks: { en: "Tasks", zh: "任务" },
  scheduler: { en: "Scheduler", zh: "调度" },
  worker: { en: "Worker", zh: "Worker" },
  storage: { en: "Storage", zh: "存储" },
  sessions: { en: "Sessions", zh: "会话" },
  observability: { en: "Observability", zh: "观测" },
  exports: { en: "Exports", zh: "导出" },
};

const translations = {
  en: {
    admin_title: "Crawler Platform Admin",
    runtime_loading: "Loading runtime",
    refresh: "Refresh",
    previous: "Previous",
    next: "Next",
    offset: "Offset",
    total: "Total",
    done: "Done",
    yes: "yes",
    no: "no",
    unknown: "unknown",
    dashboard: "Dashboard",
    start_here: "Start here",
    try_local_example: "Try local example",
    examples: "Examples",
    spiders: "Spiders",
    tasks: "Tasks",
    example: "Example",
    title: "Title",
    runnable: "Runnable",
    no_quickstart_examples: "No quickstart examples",
    runtime: "Runtime",
    storage: "Storage",
    healthy: "healthy",
    check: "check",
    task_running: "Task running",
    task_failed: "Task failed",
    task_success: "Task success",
    worker_jobs: "Worker jobs",
    jobs_queued: "Jobs queued",
    jobs_running: "Jobs running",
    jobs_failed: "Jobs failed",
    schedules: "Schedules",
    schedules_enabled: "Schedules enabled",
    exports: "Exports",
    file_store: "FileStore",
    database_disabled: "database disabled",
    validate: "Validate",
    smoke: "Smoke",
    quickstart_examples: "Quickstart examples",
    all_examples: "All examples",
    feature: "Feature",
    no_examples: "No examples",
    copy_json: "Copy JSON",
    save_as_spider: "Save as spider",
    run_selected_example: "Run selected example",
    new: "New",
    id: "ID",
    name: "Name",
    type: "Type",
    enabled: "Enabled",
    no_spiders: "No spiders",
    format: "Format",
    save: "Save",
    run: "Run",
    delete: "Delete",
    status: "Status",
    apply: "Apply",
    task_status_placeholder: "status",
    task_spider_placeholder: "spider_id",
    spider: "Spider",
    records: "Records",
    no_tasks: "No tasks yet - run a local example first.",
    pause: "Pause",
    resume: "Resume",
    cancel: "Cancel",
    retry: "Retry",
    rerun: "Rerun",
    export: "Export",
    scheduler: "Scheduler",
    run_due: "Run due",
    enqueue_due: "Enqueue due",
    no_schedules: "No schedules",
    runs: "Runs",
    no_scheduler_runs: "No scheduler runs",
    trigger: "Trigger",
    disable: "Disable",
    worker: "Worker",
    run_once: "Run once",
    run_until_empty: "Run until empty",
    recover: "Recover",
    source: "Source",
    no_jobs: "No jobs",
    enqueue: "Enqueue",
    repair_dry_run: "Repair dry-run",
    create_snapshot: "Create snapshot",
    snapshot: "Snapshot",
    created: "Created",
    no_snapshots: "No snapshots",
    snapshot_placeholder: "snapshot_id",
    restore_dry_run: "Restore dry-run",
    sessions: "Sessions",
    updated: "Updated",
    no_sessions: "No sessions",
    clear: "Clear",
    observability: "Observability",
    task_id_placeholder: "task_id",
    job_id_placeholder: "job_id",
    scheduler_run_id_placeholder: "scheduler_run_id",
    trace_id_placeholder: "trace_id",
    task_report: "Task report",
    job_report: "Job report",
    scheduler_report: "Scheduler report",
    trace: "Trace",
    level: "Level",
    scope: "Scope",
    target: "Target",
    message: "Message",
    no_logs: "No logs",
    task_export: "Task export",
    job_export: "Job export",
    scheduler_export: "Scheduler export",
    logs_export: "Logs export",
    kind: "Kind",
    no_exports: "No exports yet - select a task and export it.",
    download: "Download",
    export_created: "Export created",
    select_row_first: "Select a row first",
    example_missing_config: "Selected example does not include a spider config",
    copied_json: "Copied JSON",
    valid: "Valid",
    invalid: "Invalid",
    start_url: "Start URL",
    start_url_placeholder: "www.bilibili.com/video/BVxxxxxx/",
    apply_start_url: "Apply URL",
    run_with_start_url: "Run with URL",
    start_url_help: "This changes only the seed page. The extraction rules still come from the spider JSON on the right.",
    start_url_required: "Enter a start URL first",
  },
  zh: {
    admin_title: "爬虫平台管理台",
    runtime_loading: "正在加载运行信息",
    refresh: "刷新",
    previous: "上一页",
    next: "下一页",
    offset: "偏移",
    total: "总数",
    done: "完成",
    yes: "是",
    no: "否",
    unknown: "未知",
    dashboard: "仪表盘",
    start_here: "从这里开始",
    try_local_example: "运行本地示例",
    examples: "示例",
    spiders: "爬虫",
    tasks: "任务",
    example: "示例",
    title: "标题",
    runnable: "可运行",
    no_quickstart_examples: "没有快速开始示例",
    runtime: "运行时",
    storage: "存储",
    healthy: "健康",
    check: "检查",
    task_running: "运行中任务",
    task_failed: "失败任务",
    task_success: "成功任务",
    worker_jobs: "Worker 作业",
    jobs_queued: "排队作业",
    jobs_running: "运行中作业",
    jobs_failed: "失败作业",
    schedules: "调度计划",
    schedules_enabled: "启用的调度",
    exports: "导出",
    file_store: "文件存储",
    database_disabled: "数据库未启用",
    validate: "校验",
    smoke: "冒烟测试",
    quickstart_examples: "快速开始示例",
    all_examples: "全部示例",
    feature: "功能",
    no_examples: "没有示例",
    copy_json: "复制 JSON",
    save_as_spider: "保存为爬虫",
    run_selected_example: "运行当前示例",
    new: "新建",
    id: "ID",
    name: "名称",
    type: "类型",
    enabled: "启用",
    no_spiders: "没有爬虫",
    format: "格式化",
    save: "保存",
    run: "运行",
    delete: "删除",
    status: "状态",
    apply: "应用",
    task_status_placeholder: "状态",
    task_spider_placeholder: "spider_id",
    spider: "爬虫",
    records: "记录数",
    no_tasks: "还没有任务，请先运行一个本地示例。",
    pause: "暂停",
    resume: "恢复",
    cancel: "取消",
    retry: "重试",
    rerun: "重新运行",
    export: "导出",
    scheduler: "调度",
    run_due: "执行到期任务",
    enqueue_due: "入队到期任务",
    no_schedules: "没有调度计划",
    runs: "运行记录",
    no_scheduler_runs: "没有调度运行记录",
    trigger: "触发",
    disable: "禁用",
    worker: "Worker",
    run_once: "执行一次",
    run_until_empty: "执行直到队列为空",
    recover: "恢复",
    source: "来源",
    no_jobs: "没有作业",
    enqueue: "入队",
    repair_dry_run: "修复预演",
    create_snapshot: "创建快照",
    snapshot: "快照",
    created: "创建时间",
    no_snapshots: "没有快照",
    snapshot_placeholder: "snapshot_id",
    restore_dry_run: "恢复预演",
    sessions: "会话",
    updated: "更新时间",
    no_sessions: "没有会话",
    clear: "清空",
    observability: "观测",
    task_id_placeholder: "task_id",
    job_id_placeholder: "job_id",
    scheduler_run_id_placeholder: "scheduler_run_id",
    trace_id_placeholder: "trace_id",
    task_report: "任务报告",
    job_report: "作业报告",
    scheduler_report: "调度报告",
    trace: "追踪",
    level: "级别",
    scope: "范围",
    target: "目标",
    message: "消息",
    no_logs: "没有日志",
    task_export: "任务导出",
    job_export: "作业导出",
    scheduler_export: "调度导出",
    logs_export: "日志导出",
    kind: "类型",
    no_exports: "还没有导出，请先选择一个任务并导出。",
    download: "下载",
    export_created: "导出已创建",
    select_row_first: "请先选择一行",
    example_missing_config: "当前示例不包含爬虫配置",
    copied_json: "已复制 JSON",
    valid: "有效",
    invalid: "无效",
    start_url: "起始链接",
    start_url_placeholder: "www.bilibili.com/video/BVxxxxxx/",
    apply_start_url: "应用链接",
    run_with_start_url: "用该链接运行",
    start_url_help: "这里改的只是起始页，真正的抽取规则仍然来自右侧这份爬虫 JSON 配置。",
    start_url_required: "请先输入起始链接",
  },
};

const state = {
  activeView: "dashboard",
  locale: loadLocale(),
  selectedExample: null,
  examplesResult: null,
  selectedSpider: null,
  selectedTask: null,
  selectedJob: null,
  selectedSchedule: null,
  selectedSession: null,
  selectedExport: null,
  pages: {
    tasks: { limit: 20, offset: 0 },
    results: { limit: 20, offset: 0 },
    logs: { limit: 20, offset: 0 },
  },
};

const defaultSpiderConfig = {
  id: "new-spider",
  name: "New Spider",
  type: "http",
  start_urls: ["examples/fixtures/local_html_list.html"],
  item_selector: "article",
  unique_fields: ["title"],
  fields: [{ name: "title", type: "css", selector: "h1" }],
};

const defaultScheduleConfig = {
  spider: defaultSpiderConfig,
};

const defaultWorkerJob = {
  spider_id: "",
  source: "admin",
  priority: 0,
};

function loadLocale() {
  const locale = window.localStorage.getItem(localeStorageKey);
  return locale === "zh" ? "zh" : "en";
}

function saveLocale(locale) {
  window.localStorage.setItem(localeStorageKey, locale);
}

function t(key) {
  return translations[state.locale]?.[key] || translations.en[key] || key;
}

function yesNo(value) {
  return value ? t("yes") : t("no");
}

function pagerLabels() {
  return {
    previous: t("previous"),
    next: t("next"),
    offset: t("offset"),
    total: t("total"),
  };
}

function updateStaticText() {
  document.documentElement.lang = state.locale === "zh" ? "zh-CN" : "en";
  document.title = t("admin_title");
  adminTitle.textContent = t("admin_title");
  runtimeSummary.textContent = t("runtime_loading");
  refreshButton.textContent = t("refresh");
  languageToggleButton.textContent = state.locale === "zh" ? "EN" : "中文";
  document.querySelectorAll(".nav-item").forEach((button) => {
    const labels = navLabels[button.dataset.view];
    if (labels) {
      button.textContent = labels[state.locale];
    }
  });
}

function toggleLocale() {
  state.locale = state.locale === "zh" ? "en" : "zh";
  saveLocale(state.locale);
  updateStaticText();
  refreshRuntimeSummary();
  render();
}

function showLoading(active) {
  loadingBar.hidden = !active;
  refreshButton.disabled = active;
}

function showToast(message, tone = "info") {
  toast.textContent = message;
  toast.dataset.tone = tone;
  toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 3200);
}

async function runAction(label, action) {
  showLoading(true);
  try {
    const result = await action();
    if (label) {
      showToast(label, "success");
    }
    return result;
  } catch (error) {
    const message = error instanceof ApiClientError ? `${error.code}: ${error.message}` : error.message;
    showToast(message, "error");
    return null;
  } finally {
    showLoading(false);
  }
}

async function refreshRuntimeSummary() {
  const runtime = await apiGet("/runtime/info").catch(() => null);
  if (runtime) {
    runtimeSummary.textContent = `${runtime.data.name} ${runtime.data.version} | ${t("file_store")} | ${t("database_disabled")}`;
  }
}

function setActiveView(name) {
  state.activeView = name;
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === name);
  });
  return render();
}

async function render() {
  await runAction("", async () => {
    if (state.activeView === "dashboard") await renderDashboard();
    if (state.activeView === "examples") await renderExamples();
    if (state.activeView === "spiders") await renderSpiders();
    if (state.activeView === "tasks") await renderTasks();
    if (state.activeView === "scheduler") await renderScheduler();
    if (state.activeView === "worker") await renderWorker();
    if (state.activeView === "storage") await renderStorage();
    if (state.activeView === "sessions") await renderSessions();
    if (state.activeView === "observability") await renderObservability();
    if (state.activeView === "exports") await renderExports();
  });
}

async function safeData(path, params) {
  try {
    return (await apiGet(path, params)).data;
  } catch {
    return null;
  }
}

function countBy(items, key) {
  return (items || []).reduce((acc, item) => {
    const value = item?.[key] || t("unknown");
    acc[value] = (acc[value] || 0) + 1;
    return acc;
  }, {});
}

function metric(label, value) {
  return `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function readSpiderEditorConfig() {
  return parseJsonEditor(document.getElementById("spiderEditor").value, defaultSpiderConfig);
}

function writeSpiderEditorConfig(config) {
  document.getElementById("spiderEditor").value = formatJson(config);
}

function readStartUrlInput() {
  return String(document.getElementById("spiderStartUrl")?.value || "").trim();
}

function applyStartUrl(config, startUrl) {
  return {
    ...config,
    start_urls: [startUrl],
  };
}

async function renderDashboard() {
  const [runtime, capabilities, storage, examples, tasks, jobs, schedules, exportsList, logs] = await Promise.all([
    safeData("/runtime/info"),
    safeData("/runtime/capabilities"),
    safeData("/runtime/storage"),
    safeData("/examples"),
    safeData("/tasks"),
    safeData("/worker/jobs"),
    safeData("/scheduler/schedules"),
    safeData("/exports"),
    safeData("/observability/logs", { limit: 5 }),
  ]);
  const taskCounts = countBy(tasks, "status");
  const jobCounts = countBy(jobs, "status");
  const scheduleCounts = countBy(schedules, "status");
  view.innerHTML = `
    <div class="view-header"><h2>${t("dashboard")}</h2>${renderToolbar([{ label: t("refresh"), action: "refresh", primary: true }])}</div>
    <section class="start-here">
      <div>
        <h3>${t("start_here")}</h3>
        <div class="inline-actions">
          <button type="button" class="primary" data-action="try-local-example">${t("try_local_example")}</button>
          <button type="button" data-action="open-examples">${t("examples")}</button>
          <button type="button" data-action="open-spiders">${t("spiders")}</button>
          <button type="button" data-action="open-tasks">${t("tasks")}</button>
        </div>
      </div>
      <div>${renderTable([
        { label: t("example"), key: "id" },
        { label: t("title"), key: "title" },
        { label: t("runnable"), value: (row) => yesNo(row.runnable) },
      ], (examples || []).filter((item) => quickstartExampleIds.has(item.id)), t("no_quickstart_examples"))}</div>
    </section>
    <section class="metrics-grid">
      ${metric(t("runtime"), runtime?.version || t("unknown"))}
      ${metric(t("storage"), storage?.health?.ok ? t("healthy") : t("check"))}
      ${metric(t("examples"), (examples || []).length)}
      ${metric(t("tasks"), (tasks || []).length)}
      ${metric(t("task_running"), taskCounts.running || 0)}
      ${metric(t("task_failed"), taskCounts.failed || 0)}
      ${metric(t("task_success"), taskCounts.success || 0)}
      ${metric(t("worker_jobs"), (jobs || []).length)}
      ${metric(t("jobs_queued"), jobCounts.queued || 0)}
      ${metric(t("jobs_running"), jobCounts.running || 0)}
      ${metric(t("jobs_failed"), jobCounts.failed || 0)}
      ${metric(t("schedules"), (schedules || []).length)}
      ${metric(t("schedules_enabled"), scheduleCounts.enabled || 0)}
      ${metric(t("exports"), (exportsList || []).length)}
    </section>
    <section class="split">
      <div>${renderJson({ runtime, capabilities })}</div>
      <div>${renderJson({ recent_logs: logs || [] })}</div>
    </section>
  `;
}

async function renderExamples() {
  const examples = (await apiGet("/examples")).data || [];
  const quickstartExamples = examples.filter((item) => quickstartExampleIds.has(item.id));
  if (!state.selectedExample && (quickstartExamples[0] || examples[0])) {
    state.selectedExample = (quickstartExamples[0] || examples[0]).id;
  }
  const selected = state.selectedExample ? await safeData(`/examples/${encodeURIComponent(state.selectedExample)}`) : null;
  view.innerHTML = `
    <div class="view-header"><h2>${t("examples")}</h2>${renderToolbar([
      { label: t("validate"), action: "examples-validate" },
      { label: t("smoke"), action: "examples-smoke" },
      { label: t("refresh"), action: "refresh", primary: true },
    ])}</div>
    <section class="split">
      <div>
        <h3>${t("quickstart_examples")}</h3>
        ${renderTable([
          { label: t("id"), key: "id" },
          { label: t("title"), key: "title" },
          { label: t("feature"), key: "feature" },
          { label: t("runnable"), value: (row) => yesNo(row.runnable) },
        ], quickstartExamples, t("no_quickstart_examples"))}
        <h3>${t("all_examples")}</h3>
        ${renderTable([
          { label: t("id"), key: "id" },
          { label: t("title"), key: "title" },
          { label: t("feature"), key: "feature" },
          { label: t("runnable"), value: (row) => yesNo(row.runnable) },
        ], examples, t("no_examples"))}
      </div>
      <div>
        <div class="inline-actions">
          <button type="button" data-action="copy-example-json">${t("copy_json")}</button>
          <button type="button" data-action="save-example-spider">${t("save_as_spider")}</button>
          <button type="button" class="primary" data-action="run-selected-example">${t("run_selected_example")}</button>
        </div>
        ${renderJson(selected || {})}
        <div id="examplesStatus">${state.examplesResult ? renderJson(state.examplesResult) : ""}</div>
      </div>
    </section>
  `;
}

async function renderSpiders() {
  const spidersResponse = await apiGet("/spiders", { sort_by: "id" });
  const spiders = spidersResponse.data || [];
  if (!state.selectedSpider && spiders[0]) {
    state.selectedSpider = spiders[0].id;
  }
  const selected = state.selectedSpider ? await safeData(`/spiders/${encodeURIComponent(state.selectedSpider)}`) : null;
  const startUrl = selected?.start_urls?.[0] || "";
  view.innerHTML = `
    <div class="view-header"><h2>${t("spiders")}</h2>${renderToolbar([{ label: t("new"), action: "new-spider" }, { label: t("refresh"), action: "refresh", primary: true }])}</div>
    <section class="split">
      <div>
        ${renderTable([
          { label: t("id"), key: "id" },
          { label: t("name"), key: "name" },
          { label: t("type"), key: "type" },
          { label: t("enabled"), value: (row) => yesNo(row.enabled !== false) },
        ], spiders, t("no_spiders"))}
      </div>
      <div class="editor-panel">
        <div class="inline-actions">
          <button type="button" data-action="format-spider">${t("format")}</button>
          <button type="button" data-action="validate-spider">${t("validate")}</button>
          <button type="button" data-action="save-spider" class="primary">${t("save")}</button>
          <button type="button" data-action="run-spider">${t("run")}</button>
          <button type="button" data-action="delete-spider">${t("delete")}</button>
        </div>
        <div class="seed-runner">
          <input id="spiderStartUrl" type="url" placeholder="${escapeHtml(t("start_url_placeholder"))}" value="${escapeHtml(startUrl)}">
          <button type="button" data-action="apply-spider-start-url">${t("apply_start_url")}</button>
          <button type="button" class="primary" data-action="run-spider-with-start-url">${t("run_with_start_url")}</button>
        </div>
        <p class="helper-text">${t("start_url_help")}</p>
        <textarea id="spiderEditor" spellcheck="false">${escapeHtml(formatJson(selected || defaultSpiderConfig))}</textarea>
        <div id="spiderStatus" class="status-line" hidden></div>
        <div id="spiderCanonical">${selected ? renderJson(selected) : ""}</div>
      </div>
    </section>
  `;
}

async function renderTasks() {
  const page = state.pages.tasks;
  const filters = readTaskFilters();
  const tasksResponse = await apiGet("/tasks", { limit: page.limit, offset: page.offset, ...filters });
  const tasks = tasksResponse.data || [];
  if (!state.selectedTask && tasks[0]) {
    state.selectedTask = tasks[0].id;
  }
  const selectedId = state.selectedTask;
  const detail = selectedId ? await safeData(`/tasks/${encodeURIComponent(selectedId)}`) : null;
  const results = selectedId ? await apiGet(`/tasks/${encodeURIComponent(selectedId)}/results`, state.pages.results).catch(() => ({ data: [], meta: {} })) : { data: [], meta: {} };
  const report = selectedId ? await safeData(`/tasks/${encodeURIComponent(selectedId)}/report`) : null;
  const logs = selectedId ? await safeData(`/tasks/${encodeURIComponent(selectedId)}/logs`, { limit: 20 }) : null;
  const metrics = selectedId ? await safeData(`/tasks/${encodeURIComponent(selectedId)}/metrics`) : null;
  view.innerHTML = `
    <div class="view-header"><h2>${t("tasks")}</h2>${renderToolbar([{ label: t("refresh"), action: "refresh", primary: true }])}</div>
    <section class="filters">
      <input id="taskStatusFilter" placeholder="${escapeHtml(t("task_status_placeholder"))}" value="${escapeHtml(filters.status || "")}">
      <input id="taskSpiderFilter" placeholder="${escapeHtml(t("task_spider_placeholder"))}" value="${escapeHtml(filters.spider_id || "")}">
      <button type="button" data-action="apply-task-filters">${t("apply")}</button>
    </section>
    <section class="split">
      <div>
        ${renderTable([
          { label: t("id"), key: "id" },
          { label: t("spider"), key: "spider_id" },
          { label: t("status"), key: "status" },
          { label: t("records"), key: "saved_records" },
        ], tasks, t("no_tasks"))}
        ${renderPager("tasks", tasksResponse.meta?.pagination, pagerLabels())}
      </div>
      <div>
        <div class="inline-actions">
          <button type="button" data-action="pause-task">${t("pause")}</button>
          <button type="button" data-action="resume-task">${t("resume")}</button>
          <button type="button" data-action="cancel-task">${t("cancel")}</button>
          <button type="button" data-action="retry-task">${t("retry")}</button>
          <button type="button" data-action="rerun-task">${t("rerun")}</button>
          <button type="button" data-action="export-task">${t("export")}</button>
        </div>
        ${renderJson({ detail, results: results.data, report, logs, metrics })}
        ${renderPager("results", results.meta?.pagination, pagerLabels())}
      </div>
    </section>
  `;
}

function readTaskFilters() {
  const status = document.getElementById("taskStatusFilter")?.value || "";
  const spiderId = document.getElementById("taskSpiderFilter")?.value || "";
  return { status, spider_id: spiderId };
}

async function renderScheduler() {
  const schedules = (await apiGet("/scheduler/schedules")).data || [];
  const runs = (await apiGet("/scheduler/runs").catch(() => ({ data: [] }))).data || [];
  if (!state.selectedSchedule && schedules[0]) {
    state.selectedSchedule = schedules[0].id;
  }
  const selected = state.selectedSchedule ? await safeData(`/scheduler/schedules/${encodeURIComponent(state.selectedSchedule)}`) : null;
  view.innerHTML = `
    <div class="view-header"><h2>${t("scheduler")}</h2>${renderToolbar([{ label: t("run_due"), action: "run-due" }, { label: t("enqueue_due"), action: "enqueue-due" }, { label: t("refresh"), action: "refresh", primary: true }])}</div>
    <section class="split">
      <div>
        ${renderTable([
          { label: t("id"), key: "id" },
          { label: t("spider"), key: "spider_id" },
          { label: t("status"), key: "status" },
          { label: t("type"), key: "type" },
        ], schedules, t("no_schedules"))}
        <h3>${t("runs")}</h3>
        ${renderTable([
          { label: t("run"), key: "scheduler_run_id" },
          { label: t("scheduler"), key: "schedule_id" },
          { label: t("status"), key: "status" },
        ], runs, t("no_scheduler_runs"))}
      </div>
      <div class="editor-panel">
        <div class="inline-actions">
          <button type="button" data-action="format-schedule">${t("format")}</button>
          <button type="button" data-action="save-schedule" class="primary">${t("save")}</button>
          <button type="button" data-action="trigger-schedule">${t("trigger")}</button>
          <button type="button" data-action="pause-schedule">${t("pause")}</button>
          <button type="button" data-action="resume-schedule">${t("resume")}</button>
          <button type="button" data-action="disable-schedule">${t("disable")}</button>
        </div>
        <textarea id="scheduleEditor" spellcheck="false">${escapeHtml(formatJson(selected ? { spider: selected.spider || selected } : defaultScheduleConfig))}</textarea>
        ${selected ? renderJson(selected) : ""}
      </div>
    </section>
  `;
}

async function renderWorker() {
  const jobs = (await apiGet("/worker/jobs")).data || [];
  const stats = await safeData("/worker/stats");
  const deadLetters = await safeData("/worker/dead-letters");
  if (!state.selectedJob && jobs[0]) {
    state.selectedJob = jobs[0].id || jobs[0].job_id;
  }
  const selectedId = state.selectedJob;
  const detail = selectedId ? await safeData(`/worker/jobs/${encodeURIComponent(selectedId)}`) : null;
  const events = selectedId ? await safeData(`/worker/jobs/${encodeURIComponent(selectedId)}/events`) : null;
  view.innerHTML = `
    <div class="view-header"><h2>${t("worker")}</h2>${renderToolbar([{ label: t("run_once"), action: "worker-run-once" }, { label: t("run_until_empty"), action: "worker-run-until-empty" }, { label: t("recover"), action: "worker-recover" }, { label: t("refresh"), action: "refresh", primary: true }])}</div>
    <section class="split">
      <div>
        ${renderTable([
          { label: t("id"), value: (row) => row.id || row.job_id },
          { label: t("spider"), key: "spider_id" },
          { label: t("status"), key: "status" },
          { label: t("source"), key: "source" },
        ], jobs, t("no_jobs"))}
        ${renderJson({ stats, dead_letters: deadLetters })}
      </div>
      <div class="editor-panel">
        <div class="inline-actions">
          <button type="button" data-action="format-job">${t("format")}</button>
          <button type="button" data-action="enqueue-job" class="primary">${t("enqueue")}</button>
          <button type="button" data-action="pause-job">${t("pause")}</button>
          <button type="button" data-action="resume-job">${t("resume")}</button>
          <button type="button" data-action="cancel-job">${t("cancel")}</button>
          <button type="button" data-action="retry-job">${t("retry")}</button>
        </div>
        <textarea id="jobEditor" spellcheck="false">${escapeHtml(formatJson(defaultWorkerJob))}</textarea>
        ${renderJson({ detail, events })}
      </div>
    </section>
  `;
}

async function renderStorage() {
  const health = (await apiGet("/storage/health")).data;
  const snapshots = (await apiGet("/storage/snapshots")).data || [];
  view.innerHTML = `
    <div class="view-header"><h2>${t("storage")}</h2>${renderToolbar([{ label: t("repair_dry_run"), action: "storage-repair" }, { label: t("create_snapshot"), action: "storage-snapshot" }, { label: t("refresh"), action: "refresh", primary: true }])}</div>
    <section class="split">
      <div>${renderJson({ health })}</div>
      <div>
        ${renderTable([
          { label: t("snapshot"), key: "snapshot_id" },
          { label: t("name"), key: "name" },
          { label: t("created"), key: "created_at" },
        ], snapshots, t("no_snapshots"))}
        <input id="snapshotId" placeholder="${escapeHtml(t("snapshot_placeholder"))}">
        <button type="button" data-action="restore-snapshot">${t("restore_dry_run")}</button>
      </div>
    </section>
  `;
}

async function renderSessions() {
  const sessions = (await apiGet("/sessions")).data || [];
  const events = (await apiGet("/sessions/events").catch(() => ({ data: [] }))).data || [];
  if (!state.selectedSession && sessions[0]) {
    state.selectedSession = sessions[0].profile_id || sessions[0].id;
  }
  const selectedId = state.selectedSession;
  const detail = selectedId ? await safeData(`/sessions/${encodeURIComponent(selectedId)}`) : null;
  view.innerHTML = `
    <div class="view-header"><h2>${t("sessions")}</h2>${renderToolbar([{ label: t("refresh"), action: "refresh", primary: true }])}</div>
    <section class="split">
      <div>
        ${renderTable([
          { label: t("name"), value: (row) => row.profile_id || row.id },
          { label: t("updated"), key: "updated_at" },
        ], sessions, t("no_sessions"))}
        ${renderJson({ events })}
      </div>
      <div>
        <div class="inline-actions">
          <button type="button" data-action="clear-session">${t("clear")}</button>
          <button type="button" data-action="delete-session">${t("delete")}</button>
        </div>
        ${renderJson(detail)}
      </div>
    </section>
  `;
}

async function renderObservability() {
  const logs = (await apiGet("/observability/logs", state.pages.logs)).data || [];
  const metrics = await safeData("/observability/metrics");
  view.innerHTML = `
    <div class="view-header"><h2>${t("observability")}</h2>${renderToolbar([{ label: t("refresh"), action: "refresh", primary: true }])}</div>
    <section class="filters">
      <input id="reportTaskId" placeholder="${escapeHtml(t("task_id_placeholder"))}">
      <input id="reportJobId" placeholder="${escapeHtml(t("job_id_placeholder"))}">
      <input id="reportSchedulerRunId" placeholder="${escapeHtml(t("scheduler_run_id_placeholder"))}">
      <input id="traceId" placeholder="${escapeHtml(t("trace_id_placeholder"))}">
      <button type="button" data-action="load-task-report">${t("task_report")}</button>
      <button type="button" data-action="load-job-report">${t("job_report")}</button>
      <button type="button" data-action="load-scheduler-report">${t("scheduler_report")}</button>
      <button type="button" data-action="load-trace">${t("trace")}</button>
    </section>
    <section class="split">
      <div>${renderTable([
        { label: t("level"), key: "level" },
        { label: t("scope"), key: "scope" },
        { label: t("target"), key: "target_id" },
        { label: t("message"), key: "message" },
      ], logs, t("no_logs"))}</div>
      <div id="observabilityOutput">${renderJson({ metrics })}</div>
    </section>
  `;
}

async function renderExports() {
  const exportsList = (await apiGet("/exports")).data || [];
  if (!state.selectedExport && exportsList[0]) {
    state.selectedExport = exportsList[0].export_id || exportsList[0].id;
  }
  const selectedId = state.selectedExport;
  const detail = selectedId ? await safeData(`/exports/${encodeURIComponent(selectedId)}`) : null;
  view.innerHTML = `
    <div class="view-header"><h2>${t("exports")}</h2>${renderToolbar([{ label: t("refresh"), action: "refresh", primary: true }])}</div>
    <section class="filters">
      <input id="exportTaskId" placeholder="${escapeHtml(t("task_id_placeholder"))}">
      <input id="exportJobId" placeholder="${escapeHtml(t("job_id_placeholder"))}">
      <input id="exportSchedulerRunId" placeholder="${escapeHtml(t("scheduler_run_id_placeholder"))}">
      <button type="button" data-action="create-task-export">${t("task_export")}</button>
      <button type="button" data-action="create-job-export">${t("job_export")}</button>
      <button type="button" data-action="create-scheduler-export">${t("scheduler_export")}</button>
      <button type="button" data-action="create-logs-export">${t("logs_export")}</button>
    </section>
    <section class="split">
      <div>${renderTable([
        { label: t("id"), value: (row) => row.export_id || row.id },
        { label: t("kind"), key: "kind" },
        { label: t("format"), key: "format" },
        { label: t("created"), key: "created_at" },
      ], exportsList, t("no_exports"))}</div>
      <div>
        <div class="inline-actions">
          <a class="button-link" href="${selectedId ? `/exports/${encodeURIComponent(selectedId)}/download` : "#"}">${t("download")}</a>
          <button type="button" data-action="delete-export">${t("delete")}</button>
        </div>
        ${renderJson(detail)}
      </div>
    </section>
  `;
}

function selectedId(name) {
  const value = state[name];
  if (!value) {
    showToast(t("select_row_first"), "error");
  }
  return value;
}

async function selectedExampleConfig() {
  const exampleId = state.selectedExample || "local-api-json";
  const selected = await safeData(`/examples/${encodeURIComponent(exampleId)}`);
  if (!selected?.config) {
    throw new Error(t("example_missing_config"));
  }
  return selected.config;
}

async function saveSelectedExampleSpider() {
  const config = await selectedExampleConfig();
  const result = await apiPost("/spiders", config);
  state.selectedSpider = result.data.id;
  return result.data;
}

async function runSelectedExample() {
  const spider = await saveSelectedExampleSpider();
  const result = await apiPost("/tasks/run", { spider_id: spider.id });
  state.selectedTask = result.data.task_id || result.data.id;
  return result.data;
}

async function handleAction(action) {
  await runAction(t("done"), async () => {
    if (action === "refresh") return render();
    if (action === "open-examples") return setActiveView("examples");
    if (action === "open-spiders") return setActiveView("spiders");
    if (action === "open-tasks") return setActiveView("tasks");
    if (action === "try-local-example") {
      state.selectedExample = "local-api-json";
      await runSelectedExample();
      return setActiveView("tasks");
    }
    if (action === "examples-validate") {
      state.examplesResult = (await apiPost("/examples/validate", {})).data;
      return renderExamples();
    }
    if (action === "examples-smoke") {
      state.examplesResult = (await apiPost("/examples/smoke", { data_dir: "data/admin-example-smoke" })).data;
      return renderExamples();
    }
    if (action === "copy-example-json") {
      const selected = state.selectedExample ? await safeData(`/examples/${encodeURIComponent(state.selectedExample)}`) : null;
      if (selected?.config) {
        await navigator.clipboard.writeText(formatJson(selected.config));
        showToast(t("copied_json"), "success");
      }
      return;
    }
    if (action === "save-example-spider") {
      await saveSelectedExampleSpider();
      return setActiveView("spiders");
    }
    if (action === "run-selected-example") {
      await runSelectedExample();
      return setActiveView("tasks");
    }
    if (action === "new-spider") {
      state.selectedSpider = null;
      return renderSpiders();
    }
    if (action === "format-spider") {
      writeSpiderEditorConfig(readSpiderEditorConfig());
      return;
    }
    if (action === "apply-spider-start-url") {
      const startUrl = readStartUrlInput();
      if (!startUrl) {
        throw new Error(t("start_url_required"));
      }
      writeSpiderEditorConfig(applyStartUrl(readSpiderEditorConfig(), startUrl));
      setStatus(document.getElementById("spiderStatus"), `${t("start_url")}: ${startUrl}`, "success");
      return;
    }
    if (action === "validate-spider") {
      const payload = readSpiderEditorConfig();
      const result = await apiPost("/spiders/validate", payload);
      setStatus(document.getElementById("spiderStatus"), result.data.valid ? t("valid") : t("invalid"), result.data.valid ? "success" : "error");
      document.getElementById("spiderCanonical").innerHTML = renderJson(result.data);
      return;
    }
    if (action === "save-spider") {
      const payload = readSpiderEditorConfig();
      const result = state.selectedSpider ? await apiPut(`/spiders/${encodeURIComponent(payload.id)}`, payload) : await apiPost("/spiders", payload);
      state.selectedSpider = result.data.id;
      return renderSpiders();
    }
    if (action === "delete-spider") {
      const id = selectedId("selectedSpider");
      if (id) await apiDelete(`/spiders/${encodeURIComponent(id)}`);
      state.selectedSpider = null;
      return renderSpiders();
    }
    if (action === "run-spider") {
      const payload = readSpiderEditorConfig();
      await apiPost("/tasks/run", { spider_id: payload.id });
      return renderSpiders();
    }
    if (action === "run-spider-with-start-url") {
      const startUrl = readStartUrlInput();
      if (!startUrl) {
        throw new Error(t("start_url_required"));
      }
      const payload = applyStartUrl(readSpiderEditorConfig(), startUrl);
      const spiderId = state.selectedSpider || payload.id;
      writeSpiderEditorConfig(payload);
      const result = spiderId
        ? await apiPost(`/tasks/run/${encodeURIComponent(spiderId)}?start_url=${encodeURIComponent(startUrl)}`)
        : await apiPost("/tasks/run", { spider: payload });
      state.selectedSpider = payload.id;
      state.selectedTask = result.data.task_id || result.data.id;
      return setActiveView("tasks");
    }
    if (action === "apply-task-filters") {
      state.pages.tasks.offset = 0;
      return renderTasks();
    }
    if (["pause-task", "resume-task", "cancel-task", "retry-task", "rerun-task"].includes(action)) {
      const verb = action.replace("-task", "");
      const id = selectedId("selectedTask");
      if (id) await apiPost(`/tasks/${encodeURIComponent(id)}/${verb}`, { reason: "admin" });
      return renderTasks();
    }
    if (action === "export-task") {
      const id = selectedId("selectedTask");
      if (!id) {
        return;
      }
      const result = await apiPost(`/exports/tasks/${encodeURIComponent(id)}`, { format: "jsonl" });
      state.selectedExport = result.data.export_id || result.data.id || null;
      showToast(t("export_created"), "success");
      return setActiveView("exports");
    }
    if (action === "format-schedule") {
      document.getElementById("scheduleEditor").value = formatJson(parseJsonEditor(document.getElementById("scheduleEditor").value, defaultScheduleConfig));
      return;
    }
    if (action === "save-schedule") {
      await apiPost("/scheduler/schedules", parseJsonEditor(document.getElementById("scheduleEditor").value, defaultScheduleConfig));
      return renderScheduler();
    }
    if (action === "run-due") {
      await apiPost("/scheduler/run-due", {});
      return renderScheduler();
    }
    if (action === "enqueue-due") {
      await apiPost("/scheduler/enqueue-due", {});
      return renderScheduler();
    }
    if (["trigger-schedule", "pause-schedule", "resume-schedule", "disable-schedule"].includes(action)) {
      const id = selectedId("selectedSchedule");
      const verb = action.replace("-schedule", "");
      if (id) await apiPost(`/scheduler/schedules/${encodeURIComponent(id)}/${verb}`);
      return renderScheduler();
    }
    if (action === "format-job") {
      document.getElementById("jobEditor").value = formatJson(parseJsonEditor(document.getElementById("jobEditor").value, defaultWorkerJob));
      return;
    }
    if (action === "enqueue-job") {
      await apiPost("/worker/jobs", parseJsonEditor(document.getElementById("jobEditor").value, defaultWorkerJob));
      return renderWorker();
    }
    if (action === "worker-run-once") {
      await apiPost("/worker/run-once", {});
      return renderWorker();
    }
    if (action === "worker-run-until-empty") {
      await apiPost("/worker/run-until-empty", {});
      return renderWorker();
    }
    if (action === "worker-recover") {
      await apiPost("/worker/recover", {});
      return renderWorker();
    }
    if (["pause-job", "resume-job", "cancel-job", "retry-job"].includes(action)) {
      const id = selectedId("selectedJob");
      const verb = action.replace("-job", "");
      if (id) await apiPost(`/worker/jobs/${encodeURIComponent(id)}/${verb}`, { reason: "admin" });
      return renderWorker();
    }
    if (action === "storage-repair") {
      await apiPost("/storage/repair?dry_run=true");
      return renderStorage();
    }
    if (action === "storage-snapshot") {
      await apiPost("/storage/snapshots?name=admin");
      return renderStorage();
    }
    if (action === "restore-snapshot") {
      const id = document.getElementById("snapshotId").value;
      if (id) await apiPost(`/storage/snapshots/${encodeURIComponent(id)}/restore?dry_run=true`);
      return renderStorage();
    }
    if (action === "clear-session" || action === "delete-session") {
      const id = selectedId("selectedSession");
      if (id && action === "clear-session") await apiPost(`/sessions/${encodeURIComponent(id)}/clear`);
      if (id && action === "delete-session") await apiDelete(`/sessions/${encodeURIComponent(id)}`);
      return renderSessions();
    }
    if (action === "load-task-report") return loadObservability(`/observability/reports/tasks/${encodeURIComponent(document.getElementById("reportTaskId").value)}`);
    if (action === "load-job-report") return loadObservability(`/observability/reports/jobs/${encodeURIComponent(document.getElementById("reportJobId").value)}`);
    if (action === "load-scheduler-report") return loadObservability(`/observability/reports/scheduler/${encodeURIComponent(document.getElementById("reportSchedulerRunId").value)}`);
    if (action === "load-trace") return loadObservability(`/observability/traces/${encodeURIComponent(document.getElementById("traceId").value)}`);
    if (action === "create-task-export") {
      const result = await apiPost(`/exports/tasks/${encodeURIComponent(document.getElementById("exportTaskId").value)}`, { format: "jsonl" });
      state.selectedExport = result.data.export_id || result.data.id || null;
      return renderExports();
    }
    if (action === "create-job-export") {
      const result = await apiPost(`/exports/jobs/${encodeURIComponent(document.getElementById("exportJobId").value)}`, { format: "jsonl" });
      state.selectedExport = result.data.export_id || result.data.id || null;
      return renderExports();
    }
    if (action === "create-scheduler-export") {
      const result = await apiPost(`/exports/scheduler/${encodeURIComponent(document.getElementById("exportSchedulerRunId").value)}`, { format: "jsonl" });
      state.selectedExport = result.data.export_id || result.data.id || null;
      return renderExports();
    }
    if (action === "create-logs-export") {
      const result = await apiPost("/exports/observability/logs", { format: "jsonl" });
      state.selectedExport = result.data.export_id || result.data.id || null;
      return renderExports();
    }
    if (action === "delete-export") {
      const id = selectedId("selectedExport");
      if (id) await apiDelete(`/exports/${encodeURIComponent(id)}`);
      state.selectedExport = null;
      return renderExports();
    }
  });
}

async function loadObservability(path) {
  const output = document.getElementById("observabilityOutput");
  if (!path.endsWith("/")) {
    const result = await apiGet(path);
    output.innerHTML = renderJson(result.data);
  }
}

view.addEventListener("click", (event) => {
  const actionButton = event.target.closest("[data-action]");
  if (actionButton) {
    handleAction(actionButton.dataset.action);
    return;
  }
  const pager = event.target.closest("[data-pager]");
  if (pager && event.target.dataset.offset) {
    state.pages[pager.dataset.pager].offset = Number(event.target.dataset.offset);
    render();
    return;
  }
  const row = event.target.closest("tr[data-row-id]");
  if (!row) {
    return;
  }
  const id = row.dataset.rowId;
  if (state.activeView === "examples") state.selectedExample = id;
  if (state.activeView === "spiders") state.selectedSpider = id;
  if (state.activeView === "tasks") state.selectedTask = id;
  if (state.activeView === "scheduler") state.selectedSchedule = id;
  if (state.activeView === "worker") state.selectedJob = id;
  if (state.activeView === "sessions") state.selectedSession = id;
  if (state.activeView === "exports") state.selectedExport = id;
  render();
});

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => setActiveView(button.dataset.view));
});

refreshButton.addEventListener("click", () => render());
languageToggleButton.addEventListener("click", () => toggleLocale());

updateStaticText();
refreshRuntimeSummary();
render();

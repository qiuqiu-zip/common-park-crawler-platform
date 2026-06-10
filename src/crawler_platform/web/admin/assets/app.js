import { apiDelete, apiGet, apiPost, apiPut, ApiClientError } from "./api.js?v=20260609c";
import {
  escapeHtml,
  formatJson,
  parseJsonEditor,
  renderEmptyState,
  renderJson,
  renderPager,
  renderRawJsonSection,
  renderSummaryPanel,
  renderTable,
  renderToolbar,
  setStatus,
} from "./components.js?v=20260609c";

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
  activeView: loadInitialView(),
  locale: loadLocale(),
  selectedExample: null,
  examplesResult: null,
  selectedSpider: null,
  selectedTask: null,
  selectedJob: null,
  selectedSchedule: null,
  selectedSession: null,
  selectedExport: null,
  selectedLog: null,
  spiderDraftMode: false,
  scheduleDraftMode: false,
  observabilityResult: null,
  observabilityInputs: {
    reportTaskId: "",
    reportJobId: "",
    reportSchedulerRunId: "",
    traceId: "",
  },
  exportInputs: {
    exportTaskId: "",
    exportJobId: "",
    exportSchedulerRunId: "",
  },
  pages: {
    tasks: { limit: 20, offset: 0 },
    results: { limit: 20, offset: 0 },
    logs: { limit: 20, offset: 0 },
  },
};

const defaultSpiderConfig = {
  id: "new-spider",
  name: "New Spider",
  version: "1.0",
  type: "http",
  start_urls: ["examples/fixtures/local_html_list.html"],
  item_selector: "article",
  unique_fields: ["title"],
  request: {
    response_type: "html",
  },
  scheduler: {
    enabled: false,
    type: "manual",
  },
  fields: [{ name: "title", type: "css", selector: "h1" }],
};

const defaultScheduleConfig = {
  spider: {
    ...defaultSpiderConfig,
    scheduler: {
      enabled: true,
      type: "interval",
      interval_seconds: 300,
      timezone: "UTC",
      misfire_policy: "skip",
      max_instances: 1,
    },
  },
};

const defaultWorkerJob = {
  spider_id: "",
  source: "admin",
  priority: 0,
  max_attempts: 1,
};

function loadInitialView() {
  const params = new URLSearchParams(window.location.search);
  const requested = params.get("view");
  return requested && navLabels[requested] ? requested : "dashboard";
}

function reviewEmptyMode() {
  const params = new URLSearchParams(window.location.search);
  return params.get("review_empty") === "1";
}

function syncViewQuery(name) {
  const url = new URL(window.location.href);
  if (name === "dashboard") {
    url.searchParams.delete("view");
  } else {
    url.searchParams.set("view", name);
  }
  window.history.replaceState({}, "", url);
}

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

const cleanZhNavLabels = {
  dashboard: "仪表盘",
  examples: "示例",
  spiders: "爬虫",
  tasks: "任务",
  scheduler: "调度",
  worker: "Worker",
  storage: "存储",
  sessions: "会话",
  observability: "观测",
  exports: "导出",
};

const cleanZhTranslations = {
  admin_title: "通用爬虫平台管理台",
  runtime_loading: "正在加载运行时信息",
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
  schedules_enabled: "已启用调度",
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
  start_url_help: "这里改的只是起始页，真正的抽取规则仍然来自右侧的爬虫 JSON。",
  start_url_required: "请先输入起始链接",
};

t = function tOverride(key) {
  if (state.locale === "zh" && key in cleanZhTranslations) {
    return cleanZhTranslations[key];
  }
  return translations[state.locale]?.[key] || translations.en[key] || key;
};

updateStaticText = function updateStaticTextOverride() {
  document.documentElement.lang = state.locale === "zh" ? "zh-CN" : "en";
  document.title = t("admin_title");
  adminTitle.textContent = t("admin_title");
  runtimeSummary.textContent = t("runtime_loading");
  refreshButton.textContent = t("refresh");
  languageToggleButton.textContent = state.locale === "zh" ? "EN" : "中文";
  document.querySelectorAll(".nav-item").forEach((button) => {
    const labels = navLabels[button.dataset.view];
    if (!labels) {
      return;
    }
    button.textContent = state.locale === "zh"
      ? cleanZhNavLabels[button.dataset.view] || labels.en
      : labels.en;
  });
};

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

function syncActiveNav() {
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === state.activeView);
  });
}

function setActiveView(name) {
  state.activeView = navLabels[name] ? name : "dashboard";
  syncViewQuery(state.activeView);
  syncActiveNav();
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

function displayText(value, fallback = "—") {
  if (value === undefined || value === null || value === "") {
    return fallback;
  }
  if (Array.isArray(value)) {
    return value.length ? value.join(", ") : fallback;
  }
  return String(value);
}

function titleCase(value) {
  return String(value || "")
    .replaceAll("_", " ")
    .replaceAll("-", " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatDateTime(value) {
  if (!value) {
    return "—";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return String(value);
  }
  return parsed.toLocaleString(state.locale === "zh" ? "zh-CN" : "en-US", {
    hour12: false,
  });
}

function shortId(value, prefix = 8, suffix = 4) {
  const text = String(value || "");
  if (!text) {
    return "—";
  }
  if (text.length <= prefix + suffix + 1) {
    return text;
  }
  return `${text.slice(0, prefix)}…${text.slice(-suffix)}`;
}

function shortIdHtml(value) {
  const text = String(value || "");
  if (!text) {
    return escapeHtml("—");
  }
  return `<span class="short-id" title="${escapeHtml(text)}">${escapeHtml(shortId(text))}</span>`;
}

displayText = function displayTextOverride(value, fallback = "N/A") {
  if (value === undefined || value === null || value === "") {
    return fallback;
  }
  if (Array.isArray(value)) {
    return value.length ? value.join(", ") : fallback;
  }
  if (typeof value === "object") {
    return Object.keys(value).length ? "Available" : fallback;
  }
  return String(value);
};

formatDateTime = function formatDateTimeOverride(value) {
  if (!value) {
    return "N/A";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return String(value);
  }
  return parsed.toLocaleString(state.locale === "zh" ? "zh-CN" : "en-US", {
    hour12: false,
  });
};

shortId = function shortIdOverride(value, prefix = 8, suffix = 4) {
  const text = String(value || "");
  if (!text) {
    return "N/A";
  }
  if (text.length <= prefix + suffix + 3) {
    return text;
  }
  return `${text.slice(0, prefix)}...${text.slice(-suffix)}`;
};

shortIdHtml = function shortIdHtmlOverride(value) {
  const text = String(value || "");
  if (!text) {
    return escapeHtml("N/A");
  }
  return `<span class="short-id" title="${escapeHtml(text)}">${escapeHtml(shortId(text))}</span>`;
};

function renderBadge(label, tone = "neutral") {
  return `<span class="badge-status badge-status-${escapeHtml(tone)}">${escapeHtml(label)}</span>`;
}

function renderBooleanBadge(value, trueLabel = "Enabled", falseLabel = "Disabled") {
  return renderBadge(value ? trueLabel : falseLabel, value ? "success" : "neutral");
}

function previewList(values, limit = 4) {
  const items = Array.isArray(values) ? values.filter(Boolean).map((value) => String(value)) : [];
  if (!items.length) {
    return "N/A";
  }
  const shown = items.slice(0, limit);
  return `${shown.join(", ")}${items.length > limit ? ` +${items.length - limit} more` : ""}`;
}

function countKeys(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? Object.keys(value).length : 0;
}

function inventoryRows(record, preferredKeys = []) {
  if (!record || typeof record !== "object") {
    return [];
  }
  const orderedKeys = [...new Set([...preferredKeys.filter((key) => key in record), ...Object.keys(record)])];
  return orderedKeys.map((key) => ({
    key,
    label: humanizeFieldName(key),
    value: Array.isArray(record[key]) ? record[key].length : record[key],
  }));
}

function configFieldRows(fields) {
  return (fields || []).map((field) => ({
    ...field,
    field_type: firstFilled(field.type, field.extractor, "unknown"),
    field_path: firstFilled(field.selector, field.json_path, field.attribute, field.pattern, "N/A"),
  }));
}

function exampleRunner(example) {
  return firstFilled(
    example?.runner,
    example?.requires_playwright ? "playwright" : null,
    example?.config?.type,
    example?.example_type,
    example?.template ? "template" : null,
    example?.runnable ? "manual" : null,
    "reference",
  );
}

function firstFilled(...values) {
  return values.find((value) => value !== undefined && value !== null && value !== "") ?? null;
}

function statusMeta(status) {
  const key = String(status || "unknown").toLowerCase();
  const map = {
    success: { label: "Success", tone: "success" },
    succeeded: { label: "Succeeded", tone: "success" },
    failed: { label: "Failed", tone: "failed" },
    running: { label: "Running", tone: "running" },
    cancelled: { label: "Cancelled", tone: "warning" },
    canceled: { label: "Cancelled", tone: "warning" },
    queued: { label: "Queued", tone: "warning" },
    paused: { label: "Paused", tone: "warning" },
    retrying: { label: "Retrying", tone: "warning" },
    enabled: { label: "Enabled", tone: "success" },
    disabled: { label: "Disabled", tone: "neutral" },
  };
  return map[key] || { label: titleCase(status || "Unknown"), tone: "neutral" };
}

function renderStatusBadge(status) {
  const meta = statusMeta(status);
  return `<span class="badge-status badge-status-${escapeHtml(meta.tone)}">${escapeHtml(meta.label)}</span>`;
}

function summaryField(label, value, hint = "") {
  return {
    label,
    value: displayText(value),
    hint,
  };
}

function htmlSummaryField(label, html, hint = "") {
  return {
    label,
    html,
    hint,
  };
}

function buildPreviewColumns(rows, preferredKeys = []) {
  if (!rows || !rows.length || typeof rows[0] !== "object" || Array.isArray(rows[0])) {
    return [];
  }
  const keys = Object.keys(rows[0]);
  const ordered = [...new Set([...preferredKeys.filter((key) => keys.includes(key)), ...keys])].slice(0, 5);
  return ordered.map((key) => ({
    label: titleCase(key),
    value: (row) => {
      const value = row[key];
      if (Array.isArray(value)) {
        return value.join(", ");
      }
      if (value && typeof value === "object") {
        return titleCase(key);
      }
      return displayText(value);
    },
  }));
}

function renderPreviewTable(title, rows, emptyText, preferredKeys = []) {
  const columns = buildPreviewColumns(rows, preferredKeys);
  return `
    <div class="subsection">
      <h3>${escapeHtml(title)}</h3>
      ${columns.length ? renderTable(columns, rows, emptyText, { selectable: false }) : renderEmptyState(title, emptyText)}
    </div>
  `;
}

function formatFileSize(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value < 0) {
    return displayText(bytes);
  }
  if (value < 1024) {
    return `${value} B`;
  }
  const units = ["KB", "MB", "GB", "TB"];
  let size = value / 1024;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  const precision = size >= 10 ? 0 : 1;
  return `${size.toFixed(precision)} ${units[unitIndex]}`;
}

function humanizeFieldName(name) {
  return titleCase(String(name || "").replaceAll(/[._]/g, " "));
}

function previewFieldNames(names, limit = 4) {
  const values = (names || []).slice(0, limit).map((name) => humanizeFieldName(name));
  if (!values.length) {
    return "No fields listed";
  }
  const suffix = names.length > limit ? ` +${names.length - limit} more` : "";
  return `${values.join(", ")}${suffix}`;
}

function extractSchedulerType(schedule) {
  return firstFilled(schedule?.scheduler?.type, schedule?.spider?.scheduler?.type, schedule?.type, "unknown");
}

function latestEventRows(logs) {
  return (logs || []).map((row) => ({
    ...row,
    event_time: formatDateTime(firstFilled(row.timestamp, row.created_at)),
    event_module: firstFilled(row.component, row.scope, row.logger, "runtime"),
  }));
}

function summarizeObservability(data) {
  const metrics = data?.metrics || data || {};
  const counters = metrics?.counters || {};
  return [
    summaryField("Requests total", firstFilled(counters.requests_total, data?.requests_total, 0)),
    summaryField("Requests success", firstFilled(counters.requests_success, data?.requests_success, 0)),
    summaryField("Requests failed", firstFilled(counters.requests_failed, data?.requests_failed, 0)),
    summaryField("Records saved", firstFilled(counters.records_saved, data?.records_saved, 0)),
    summaryField("Tasks succeeded", firstFilled(counters.tasks_succeeded, data?.tasks_succeeded, 0)),
    summaryField("Tasks failed", firstFilled(counters.tasks_failed, data?.tasks_failed, 0)),
  ];
}

function levelMeta(level) {
  const key = String(level || "info").toLowerCase();
  const map = {
    debug: { label: "Debug", tone: "neutral" },
    info: { label: "Info", tone: "running" },
    warning: { label: "Warning", tone: "warning" },
    error: { label: "Error", tone: "failed" },
    critical: { label: "Critical", tone: "failed" },
  };
  return map[key] || { label: titleCase(level || "Info"), tone: "neutral" };
}

function renderLevelBadge(level) {
  const meta = levelMeta(level);
  return `<span class="badge-status badge-status-${escapeHtml(meta.tone)}">${escapeHtml(meta.label)}</span>`;
}

function observabilityLogRows(logs) {
  return (logs || []).map((row) => ({
    ...row,
    source_label: firstFilled(row.scope, row.component, row.logger, "runtime"),
    target_short: shortId(row.target_id),
    logged_at: formatDateTime(firstFilled(row.timestamp, row.created_at)),
  }));
}

function observabilityRowId(row) {
  return firstFilled(
    row.event_id,
    `${firstFilled(row.timestamp, row.created_at, "no-time")}:${firstFilled(row.trace_id, row.target_id, row.message, "no-target")}`,
  );
}

function renderExportSummary(detail) {
  if (!detail) {
    return renderEmptyState("Select an export", "Choose an export on the left to inspect its manifest and download it.");
  }
  const exportId = detail.export_id || detail.id;
  const sourceId = detail.source_id || "";
  return renderSummaryPanel({
    title: "Export Summary",
    subtitle: `Current export: ${shortId(exportId)}. Download and delete actions apply to the selected export.`,
    badges: [
      { label: statusMeta(detail.status).label, tone: statusMeta(detail.status).tone },
      { label: String(detail.format || "unknown").toUpperCase(), tone: "neutral" },
    ],
    actions: `
      <a class="button-link" href="/exports/${encodeURIComponent(exportId)}/download">${escapeHtml(t("download"))}</a>
      <button type="button" data-action="delete-export">${escapeHtml(t("delete"))}</button>
    `,
    fields: [
      htmlSummaryField("Export ID", shortIdHtml(exportId), exportId),
      summaryField("Source type", humanizeFieldName(detail.source_type)),
      sourceId ? htmlSummaryField("Source ID", shortIdHtml(sourceId), sourceId) : null,
      summaryField("Status", statusMeta(detail.status).label),
      summaryField("Format", String(detail.format || "unknown").toUpperCase()),
      summaryField("Rows", firstFilled(detail.rows_count, 0)),
      summaryField("Fields", (detail.columns || []).length),
      summaryField("Field preview", previewFieldNames(detail.columns || [])),
      summaryField("Created", formatDateTime(detail.created_at)),
      summaryField("File size", formatFileSize(detail.file_size_bytes)),
      summaryField("Filename", firstFilled(detail.filename, detail.path)),
    ],
    raw: detail,
    rawLabel: "Show raw export manifest / 查看原始导出清单",
  });
}

function renderObservabilitySummary(title, data, subtitle = "") {
  return renderSummaryPanel({
    title,
    subtitle,
    fields: summarizeObservability(data),
    raw: data,
    rawLabel: "Show raw observability JSON / 查看原始 JSON",
  });
}

function buildObservabilityResult(path, data) {
  if (path.includes("/observability/traces/")) {
    return {
      kind: "trace",
      title: "Trace Timeline",
      subtitle: "Inspect the event flow for the selected trace before opening raw JSON.",
      data,
    };
  }
  if (path.includes("/observability/reports/tasks/") || /\/tasks\/.+\/report$/.test(path)) {
    return {
      kind: "report",
      title: "Task Report",
      subtitle: "Task report summary first, raw report JSON second.",
      data,
    };
  }
  if (path.includes("/observability/reports/jobs/")) {
    return {
      kind: "report",
      title: "Job Report",
      subtitle: "Job report summary first, raw report JSON second.",
      data,
    };
  }
  if (path.includes("/observability/reports/scheduler/")) {
    return {
      kind: "report",
      title: "Scheduler Report",
      subtitle: "Scheduler report summary first, raw report JSON second.",
      data,
    };
  }
  return {
    kind: "metrics",
    title: "Metrics Snapshot",
    subtitle: "Aggregate counters for the local admin workspace.",
    data,
  };
}

function renderObservabilityPayload(result) {
  if (!result?.data) {
    return renderEmptyState("No observability result", "Use the controls above to load a report, trace, or metrics snapshot.");
  }
  if (result.kind === "error") {
    return renderSummaryPanel({
      title: result.title,
      subtitle: result.subtitle,
      badges: [{ label: "Not found", tone: "warning" }],
      fields: [
        summaryField("Message", result.data.message),
        summaryField("Target ID", result.data.target_id || "N/A"),
      ],
      raw: result.data,
      rawLabel: "Show error JSON / 查看错误 JSON",
    });
  }
  if (result.kind === "metrics") {
    return renderObservabilitySummary(result.title, result.data, result.subtitle);
  }
  if (result.kind === "trace") {
    const events = result.data.events || [];
    const firstEvent = events[0] || {};
    const lastEvent = events[events.length - 1] || {};
    return `
      ${renderSummaryPanel({
        title: result.title,
        subtitle: result.subtitle,
        badges: [{ label: `${events.length} events`, tone: "neutral" }],
        fields: [
          htmlSummaryField("Trace ID", shortIdHtml(result.data.trace_id), result.data.trace_id),
          summaryField("Events", events.length),
          summaryField("Started", formatDateTime(firstEvent.timestamp)),
          summaryField("Last event", formatDateTime(lastEvent.timestamp)),
          summaryField("Task ID", shortId(firstFilled(firstEvent.task_id, lastEvent.task_id))),
          summaryField("Spider", firstFilled(firstEvent.spider_id, lastEvent.spider_id)),
        ],
        raw: result.data,
        rawLabel: "Show raw trace JSON / 查看原始 Trace JSON",
      })}
      ${renderPreviewTable("Trace timeline preview", events.slice(0, 8), "No trace events recorded yet.", ["timestamp", "event_type", "component", "message", "url"])}
    `;
  }
  const data = result.data;
  return `
    ${renderSummaryPanel({
      title: result.title,
      subtitle: result.subtitle,
      badges: data.status ? [{ label: statusMeta(data.status).label, tone: statusMeta(data.status).tone }] : [],
      fields: [
        summaryField("Target type", firstFilled(data.target_type, result.title.replace(" Report", ""))),
        htmlSummaryField("Target ID", shortIdHtml(firstFilled(data.target_id, data.task_id, data.job_id, data.scheduler_run_id)), firstFilled(data.target_id, data.task_id, data.job_id, data.scheduler_run_id)),
        summaryField("Spider", data.spider_id),
        summaryField("Status", firstFilled(data.status, "N/A")),
        summaryField("Duration", data.duration_ms ? `${Math.round(Number(data.duration_ms))} ms` : "N/A"),
        summaryField("Requests total", firstFilled(data.total_requests, 0)),
        summaryField("Requests success", firstFilled(data.success_requests, 0)),
        summaryField("Requests failed", firstFilled(data.failed_requests, 0)),
        summaryField("Records saved", firstFilled(data.saved_records, 0)),
        summaryField("Record quality", firstFilled(data.record_quality_status, "N/A")),
        summaryField("Crawl policy", firstFilled(
          data.crawl_policy_summary,
          data.crawl_policy?.enabled ? `Checked ${firstFilled(data.crawl_policy.policy_checked_urls, 0)} URLs` : "Disabled",
          "N/A",
        )),
        summaryField("Warnings", firstFilled(data.warnings_count, (data.warning_summary || []).length, 0)),
        summaryField("Errors", firstFilled(data.errors_count, (data.error_summary || []).length, 0)),
        summaryField("Trace ID", shortId(data.trace_id), data.trace_id || ""),
        summaryField("Created", formatDateTime(data.created_at)),
      ],
      raw: data,
      rawLabel: "Show raw report JSON / 查看原始报告 JSON",
    })}
    ${renderPreviewTable("Record samples", data.record_samples || [], "No record samples were captured for this report.", ["id", "title", "response_status", "source_url"])}
  `;
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

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}

async function firstAvailableSpiderConfig() {
  const response = await apiGet("/spiders", { sort_by: "id" }).catch(() => ({ data: [] }));
  const spiders = response.data || [];
  const first = spiders.find((item) => item.enabled !== false) || spiders[0];
  if (!first?.id) {
    return null;
  }
  return await safeData(`/spiders/${encodeURIComponent(first.id)}`) || first;
}

function buildSpiderDraft() {
  return cloneJson(defaultSpiderConfig);
}

function buildScheduleDraft(spider) {
  const sourceSpider = spider ? cloneJson(spider) : buildSpiderDraft();
  const sourceScheduler = sourceSpider.scheduler || {};
  return {
    spider: {
      ...sourceSpider,
      scheduler: {
        ...sourceScheduler,
        enabled: true,
        type: sourceScheduler.type && sourceScheduler.type !== "manual" ? sourceScheduler.type : "interval",
        interval_seconds: sourceScheduler.interval_seconds || 300,
        timezone: sourceScheduler.timezone || "UTC",
        misfire_policy: sourceScheduler.misfire_policy || "skip",
        max_instances: sourceScheduler.max_instances || 1,
      },
    },
  };
}

function buildWorkerJobPayload(spider) {
  return {
    ...defaultWorkerJob,
    spider_id: spider?.id || "",
  };
}

function buildObservabilityErrorResult(title, message, targetId = "") {
  return {
    kind: "error",
    title,
    subtitle: "The requested report could not be loaded.",
    data: {
      message,
      target_id: targetId,
    },
  };
}

async function fetchObservabilityResult(path, options = {}) {
  try {
    const result = await apiGet(path);
    return buildObservabilityResult(path, result.data);
  } catch (error) {
    if (error instanceof ApiClientError && error.status === 404) {
      return buildObservabilityErrorResult(options.title || "Observability result", error.message, options.targetId || "");
    }
    throw error;
  }
}

async function resolveTaskIdInput(value) {
  const taskId = String(value || "").trim();
  if (!taskId) {
    throw new Error("Task ID is required");
  }
  const tasks = (await apiGet("/tasks").catch(() => ({ data: [] }))).data || [];
  const exact = tasks.find((task) => task.id === taskId);
  if (exact?.id) {
    return exact.id;
  }
  const prefixMatches = tasks.filter((task) => task.id && String(task.id).startsWith(taskId));
  if (prefixMatches.length === 1) {
    return prefixMatches[0].id;
  }
  if (prefixMatches.length > 1) {
    throw new Error("Task ID prefix matches multiple tasks. Please enter the full task ID.");
  }
  return taskId;
}

async function loadTaskReportById(taskId) {
  const resolvedTaskId = await resolveTaskIdInput(taskId);
  state.observabilityInputs.reportTaskId = resolvedTaskId;
  return fetchObservabilityResult(`/tasks/${encodeURIComponent(resolvedTaskId)}/report`, {
    title: "Task Report",
    targetId: resolvedTaskId,
  });
}

async function launchTaskForSpider(config, options = {}) {
  const startUrl = String(options.startUrl || "").trim();
  const shouldUseInlinePayload = Boolean(options.persistInline) || state.spiderDraftMode || !state.selectedSpider;
  let result;
  if (startUrl) {
    const payload = applyStartUrl(config, startUrl);
    if (shouldUseInlinePayload) {
      result = await apiPost("/tasks/run", { spider: payload });
    } else {
      result = await apiPost(`/tasks/run/${encodeURIComponent(config.id)}?start_url=${encodeURIComponent(startUrl)}`);
    }
    state.selectedSpider = payload.id;
  } else if (shouldUseInlinePayload) {
    result = await apiPost("/tasks/run", { spider: config });
    state.selectedSpider = config.id;
  } else {
    result = await apiPost("/tasks/run", { spider_id: config.id });
  }
  state.spiderDraftMode = false;
  state.selectedTask = result.data.task_id || result.data.id;
  return result.data;
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
  const latestTask = (tasks || [])[0] || null;
  const recentEvents = latestEventRows(logs);
  view.innerHTML = `
    <div class="view-header"><h2>${t("dashboard")}</h2>${renderToolbar([{ label: t("refresh"), action: "refresh", primary: true }])}</div>
    <section class="start-here">
      <div>
        <h3>${t("start_here")}</h3>
        <p class="helper-text">Run a local example, inspect the latest task, and export a result without leaving the dashboard.</p>
        <div class="inline-actions">
          <button type="button" class="primary" data-action="try-local-example">${t("try_local_example")}</button>
          <button type="button" data-action="run-local-api-example">Run local API example</button>
          <button type="button" data-action="open-latest-task">View latest task</button>
          <button type="button" data-action="export-latest-task">Export latest result</button>
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
      <div>${renderSummaryPanel({
        title: "Runtime summary",
        subtitle: latestTask ? `Latest task: ${shortId(latestTask.id)}` : "No task has been run yet.",
        badges: [
          { label: storage?.health?.ok ? "Storage healthy" : "Storage needs attention", tone: storage?.health?.ok ? "success" : "warning" },
        ],
        fields: [
          summaryField("Runtime", runtime?.version || t("unknown")),
          summaryField("Storage root", runtime?.storage_root || t("unknown")),
          summaryField("Examples", (examples || []).length),
          summaryField("Latest task", latestTask?.id ? shortId(latestTask.id) : "No task yet", latestTask?.id || ""),
          summaryField("Latest task status", latestTask?.status || "unknown"),
          summaryField("Exports", (exportsList || []).length),
        ],
        raw: { runtime, capabilities },
        rawLabel: "View raw runtime JSON / 查看原始 JSON",
      })}</div>
      <div>
        <section class="summary-panel">
          <div class="summary-panel-header">
            <div>
              <h3>Recent events</h3>
              <p class="helper-text">Most recent lifecycle and log events from the local admin workspace.</p>
            </div>
          </div>
          ${recentEvents.length ? renderTable([
            { label: "Time", key: "event_time" },
            { label: "Level", key: "level" },
            { label: "Module", key: "event_module" },
            { label: "Message", key: "message" },
          ], recentEvents, "No recent events", { selectable: false }) : renderEmptyState("No recent events", "Run a local example to populate recent activity.")}
          ${renderRawJsonSection({ recent_logs: logs || [] }, "View raw log JSON / 查看原始 JSON")}
        </section>
      </div>
    </section>
  `;
}

async function renderExamples() {
  const examples = (await apiGet("/examples")).data || [];
  const quickstartExamples = examples.filter((item) => quickstartExampleIds.has(item.id));
  if (reviewEmptyMode()) {
    state.selectedExample = null;
  } else if (!state.selectedExample && (quickstartExamples[0] || examples[0])) {
    state.selectedExample = (quickstartExamples[0] || examples[0]).id;
  }
  const selected = state.selectedExample ? await safeData(`/examples/${encodeURIComponent(state.selectedExample)}`) : null;
  const selectedConfig = selected?.config || null;
  const selectedFieldRows = configFieldRows(selectedConfig?.fields || []);
  view.innerHTML = `
    <div class="view-header"><h2>${t("examples")}</h2>${renderToolbar([
      { label: t("validate"), action: "examples-validate" },
      { label: t("smoke"), action: "examples-smoke" },
      { label: t("refresh"), action: "refresh", primary: true },
    ])}</div>
    <section class="split">
      <div class="panel-stack">
        ${renderSummaryPanel({
          title: "Example Library",
          subtitle: "Pick a runnable example on the left, then inspect its summary before copying or running it.",
          badges: [
            { label: `${examples.length} total`, tone: "neutral" },
            { label: `${examples.filter((item) => item.runnable).length} runnable`, tone: "success" },
            { label: `${quickstartExamples.length} quickstart`, tone: "running" },
          ],
          fields: [
            summaryField("Examples", examples.length),
            summaryField("Runnable", examples.filter((item) => item.runnable).length),
            summaryField("Quickstart", quickstartExamples.length),
            summaryField("Playwright required", examples.filter((item) => item.requires_playwright).length),
            summaryField("External network", examples.filter((item) => item.requires_external_network).length),
            summaryField("Templates", examples.filter((item) => item.template).length),
          ],
          raw: { examples_total: examples.length, selected_example: state.selectedExample || null },
          rawLabel: "Show raw example library summary / 查看示例库原始摘要",
        })}
        <section class="subsection">
        <h3>${t("quickstart_examples")}</h3>
        ${renderTable([
          { label: t("id"), key: "id" },
          { label: t("title"), key: "title" },
          { label: t("feature"), key: "feature" },
          { label: t("runnable"), value: (row) => yesNo(row.runnable) },
        ], quickstartExamples, t("no_quickstart_examples"), { selectedId: state.selectedExample })}
        </section>
        <section class="subsection">
        <h3>${t("all_examples")}</h3>
        ${renderTable([
          { label: t("id"), key: "id" },
          { label: t("title"), key: "title" },
          { label: t("feature"), key: "feature" },
          { label: t("runnable"), value: (row) => yesNo(row.runnable) },
        ], examples, t("no_examples"), { selectedId: state.selectedExample })}
        </section>
      </div>
      <div class="panel-stack">
        ${selected ? renderSummaryPanel({
          title: "Example Summary",
          subtitle: `Current example: ${selected.id}. Copy its config, save it as a spider, or run it directly from here.`,
          badges: [
            { label: selected.runnable ? "Runnable" : "Reference only", tone: selected.runnable ? "success" : "neutral" },
            selected.quickstart ? { label: "Quickstart", tone: "running" } : null,
            selected.smoke ? { label: "Smoke", tone: "warning" } : null,
            { label: selected.requires_playwright ? "Playwright" : "No browser", tone: selected.requires_playwright ? "warning" : "neutral" },
            { label: selected.requires_external_network ? "External network" : "Local only", tone: selected.requires_external_network ? "warning" : "success" },
          ],
          actions: renderToolbar([
            { label: t("copy_json"), action: "copy-example-json" },
            { label: t("save_as_spider"), action: "save-example-spider" },
            { label: t("run_selected_example"), action: "run-selected-example", primary: true },
          ]),
          fields: [
            summaryField("Example ID", selected.id),
            summaryField("Title", selected.title),
            summaryField("Feature", selected.feature),
            summaryField("Runner", titleCase(exampleRunner(selected))),
            summaryField("Spider config ID", firstFilled(selectedConfig?.id, "Not embedded")),
            summaryField("Config fields", selectedFieldRows.length),
            summaryField("Expected status", firstFilled(selected.expected?.status, "Not specified")),
            summaryField("Minimum records", firstFilled(selected.expected?.min_records, "Not specified")),
            summaryField("Tags", previewList(selected.tags)),
            summaryField("Fixtures", previewList(selected.fixture_paths)),
            summaryField("Path", selected.path),
            summaryField("Path exists", yesNo(selected.path_exists)),
          ],
          raw: selected,
          rawLabel: "Show raw example JSON / 查看示例原始 JSON",
        }) : renderEmptyState("Select an example", "Choose a quickstart or library example on the left to inspect its summary.")}
        ${renderPreviewTable("Config field preview", selectedFieldRows, "This example does not expose embedded field definitions.", ["name", "field_type", "field_path", "required"])}
        <div id="examplesStatus">${state.examplesResult ? renderRawJsonSection(state.examplesResult, "Show latest validate / smoke result JSON / 查看最近校验结果 JSON", true) : ""}</div>
      </div>
    </section>
  `;
}

async function renderSpiders() {
  const spidersResponse = await apiGet("/spiders", { sort_by: "id" });
  const spiders = spidersResponse.data || [];
  if (reviewEmptyMode()) {
    state.selectedSpider = null;
  } else if (!state.spiderDraftMode && !state.selectedSpider && spiders[0]) {
    state.selectedSpider = spiders[0].id;
  }
  const selected = !state.spiderDraftMode && state.selectedSpider ? await safeData(`/spiders/${encodeURIComponent(state.selectedSpider)}`) : null;
  const editableSpider = state.spiderDraftMode ? buildSpiderDraft() : (selected || buildSpiderDraft());
  const startUrl = editableSpider.start_urls?.[0] || "";
  const spiderFieldRows = configFieldRows(editableSpider.fields || []);
  const spiderTypeLabel = titleCase(firstFilled(editableSpider.type, "unknown"));
  const schedulerType = titleCase(firstFilled(editableSpider.scheduler?.type, "manual"));
  const spiderActions = state.spiderDraftMode
    ? renderToolbar([
      { label: t("format"), action: "format-spider" },
      { label: t("validate"), action: "validate-spider" },
      { label: t("save"), action: "save-spider", primary: true },
      { label: t("run"), action: "run-spider" },
    ])
    : renderToolbar([
      { label: t("format"), action: "format-spider" },
      { label: t("validate"), action: "validate-spider" },
      { label: t("save"), action: "save-spider", primary: true },
      { label: t("run"), action: "run-spider" },
      { label: t("delete"), action: "delete-spider" },
    ]);
  view.innerHTML = `
    <div class="view-header"><h2>${t("spiders")}</h2>${renderToolbar([{ label: t("new"), action: "new-spider" }, { label: t("refresh"), action: "refresh", primary: true }])}</div>
    <section class="split">
      <div class="panel-stack">
        ${renderTable([
          { label: t("id"), key: "id" },
          { label: t("name"), key: "name" },
          { label: t("type"), key: "type" },
          { label: t("enabled"), value: (row) => yesNo(row.enabled !== false) },
        ], spiders, t("no_spiders"), { selectedId: state.selectedSpider })}
      </div>
      <div class="panel-stack">
        ${renderSummaryPanel({
          title: state.spiderDraftMode ? "New Spider" : "Spider Summary",
          subtitle: state.spiderDraftMode
            ? "New spider draft. Review the minimal valid config, then validate, save, or run it."
            : state.selectedSpider
            ? `Current spider: ${editableSpider.id}. Review the operator-facing summary before opening the full JSON editor.`
            : "New spider draft. Fill in the editor below when you are ready to save.",
          badges: [
            { label: spiderTypeLabel, tone: "neutral" },
            { label: editableSpider.enabled !== false ? "Enabled" : "Disabled", tone: editableSpider.enabled !== false ? "success" : "neutral" },
            { label: editableSpider.playwright?.enabled ? "Playwright" : "Direct fetch", tone: editableSpider.playwright?.enabled ? "warning" : "neutral" },
            { label: schedulerType, tone: "running" },
          ],
          actions: spiderActions,
          fields: [
            summaryField("Spider ID", editableSpider.id),
            summaryField("Name", editableSpider.name),
            summaryField("Type", spiderTypeLabel),
            summaryField("Primary start URL", firstFilled(editableSpider.start_urls?.[0], "Not set")),
            summaryField("Response type", firstFilled(editableSpider.request?.response_type, "auto")),
            summaryField("Scheduler type", schedulerType),
            summaryField("Field count", spiderFieldRows.length),
            summaryField("Field preview", previewList((editableSpider.fields || []).map((field) => field.name))),
            summaryField("Unique fields", previewList(editableSpider.unique_fields)),
            summaryField("Detail follow", editableSpider.detail?.enabled ? "Enabled" : "Disabled"),
            summaryField("Sessions", editableSpider.session?.enabled ? "Enabled" : "Disabled"),
            summaryField("Observability", editableSpider.observability?.enabled ? "Enabled" : "Disabled"),
          ],
          raw: editableSpider,
          rawLabel: "Show raw spider JSON / 查看爬虫原始 JSON",
        })}
        ${renderPreviewTable("Extraction field preview", spiderFieldRows, "No extraction fields are defined for this spider yet.", ["name", "field_type", "field_path", "required"])}
        <section class="subsection">
          <h3>Seed URL override</h3>
          <div class="seed-runner">
          <input id="spiderStartUrl" type="url" placeholder="${escapeHtml(t("start_url_placeholder"))}" value="${escapeHtml(startUrl)}">
          <button type="button" data-action="apply-spider-start-url">${t("apply_start_url")}</button>
          <button type="button" class="primary" data-action="run-spider-with-start-url">${t("run_with_start_url")}</button>
          </div>
          <p class="helper-text">${t("start_url_help")}</p>
        </section>
        <details class="raw-json-collapsible">
          <summary>Edit spider JSON</summary>
          <div class="details-body">
            <textarea id="spiderEditor" spellcheck="false">${escapeHtml(formatJson(editableSpider))}</textarea>
            <div id="spiderStatus" class="status-line" hidden></div>
          </div>
        </details>
        <div id="spiderCanonical">${renderRawJsonSection(editableSpider, "Canonical spider JSON / 查看标准爬虫 JSON")}</div>
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
  const taskActions = renderToolbar([
    { label: t("pause"), action: "pause-task" },
    { label: t("resume"), action: "resume-task" },
    { label: t("cancel"), action: "cancel-task" },
    { label: t("retry"), action: "retry-task" },
    { label: t("rerun"), action: "rerun-task" },
    { label: "Open report", action: "open-task-report" },
    { label: t("export"), action: "export-task", primary: true },
  ]);
  view.innerHTML = `
    <div class="view-header"><h2>${t("tasks")}</h2>${renderToolbar([{ label: t("refresh"), action: "refresh", primary: true }])}</div>
    <section class="filters">
      <label class="filter-field">
        <span>Status</span>
        <input id="taskStatusFilter" placeholder="Success / Failed / Running" value="${escapeHtml(filters.status || "")}">
      </label>
      <label class="filter-field">
        <span>Spider ID</span>
        <input id="taskSpiderFilter" placeholder="Filter by spider ID" value="${escapeHtml(filters.spider_id || "")}">
      </label>
      <button type="button" data-action="apply-task-filters">${t("apply")}</button>
    </section>
    <section class="split">
      <div>
        ${renderTable([
          { label: "Task ID", html: (row) => shortIdHtml(row.id) },
          { label: t("spider"), key: "spider_id" },
          { label: t("status"), html: (row) => renderStatusBadge(row.status) },
          { label: t("records"), key: "saved_records" },
        ], tasks, t("no_tasks"), { selectedId: state.selectedTask })}
        ${renderPager("tasks", tasksResponse.meta?.pagination, pagerLabels())}
      </div>
      <div>
        ${selectedId && detail ? renderSummaryPanel({
          title: "Task Summary",
          subtitle: `Current task: ${shortId(selectedId)}. Actions below apply to the selected task.`,
          badges: [{ label: statusMeta(detail.status).label, tone: statusMeta(detail.status).tone }],
          actions: taskActions,
          fields: [
            htmlSummaryField("Task ID", shortIdHtml(detail.id || selectedId), detail.id || selectedId),
            summaryField("Spider", detail.spider_id),
            summaryField("Status", statusMeta(detail.status).label),
            summaryField("Records", firstFilled(detail.saved_records, detail.saved_count, 0)),
            summaryField("Requests success", firstFilled(detail.success_requests, detail.total_requests_success, 0)),
            summaryField("Requests failed", firstFilled(detail.failed_requests, detail.total_requests_failed, 0)),
            summaryField("Created", formatDateTime(firstFilled(detail.created_at, detail.started_at))),
            summaryField("Finished", formatDateTime(firstFilled(detail.finished_at, detail.completed_at))),
          ],
          raw: { detail, report, logs, metrics },
          rawLabel: "Show raw task JSON / 查看原始 JSON",
        }) : renderEmptyState("Select a task", "Choose a task on the left to view its summary, results, and raw details.")}
        ${renderPreviewTable("Results for selected task", results.data, "Run a task to see extracted records here.", ["id", "title", "status", "response_status", "source_url"])}
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
  const defaultSpider = await firstAvailableSpiderConfig();
  if (!state.scheduleDraftMode && !state.selectedSchedule && schedules[0]) {
    state.selectedSchedule = schedules[0].id;
  }
  const selected = !state.scheduleDraftMode && state.selectedSchedule ? await safeData(`/scheduler/schedules/${encodeURIComponent(state.selectedSchedule)}`) : null;
  const scheduleDraft = state.scheduleDraftMode ? buildScheduleDraft(defaultSpider) : null;
  const editableSchedule = scheduleDraft || (selected ? { spider: selected.spider || selected } : defaultScheduleConfig);
  const scheduleType = extractSchedulerType(selected || editableSchedule);
  const scheduleEmptyText = "No schedules yet - create a new schedule or save a spider first.";
  const scheduleActions = state.scheduleDraftMode
    ? renderToolbar([
      { label: t("format"), action: "format-schedule" },
      { label: t("save"), action: "save-schedule", primary: true },
    ])
    : renderToolbar([
      { label: t("format"), action: "format-schedule" },
      { label: `${t("save")} ${shortId(selected?.id)}`, action: "save-schedule", primary: true },
      { label: `${t("trigger")} ${shortId(selected?.id)}`, action: "trigger-schedule" },
      { label: `${t("pause")} ${shortId(selected?.id)}`, action: "pause-schedule" },
      { label: `${t("resume")} ${shortId(selected?.id)}`, action: "resume-schedule" },
      { label: `${t("disable")} ${shortId(selected?.id)}`, action: "disable-schedule" },
    ]);
  view.innerHTML = `
    <div class="view-header"><h2>${t("scheduler")}</h2>${renderToolbar([{ label: "New schedule", action: "new-schedule" }, { label: t("run_due"), action: "run-due" }, { label: t("enqueue_due"), action: "enqueue-due" }, { label: t("refresh"), action: "refresh", primary: true }])}</div>
    <section class="split">
      <div>
        ${renderTable([
          { label: "Schedule ID", html: (row) => shortIdHtml(row.id) },
          { label: t("spider"), key: "spider_id" },
          { label: "Schedule type", value: (row) => extractSchedulerType(row) },
          { label: t("status"), html: (row) => renderStatusBadge(row.status) },
          { label: "Next run", value: (row) => formatDateTime(row.next_run_at) },
        ], schedules, scheduleEmptyText, { selectedId: state.selectedSchedule })}
        <h3>${t("runs")}</h3>
        ${renderTable([
          { label: t("run"), html: (row) => shortIdHtml(row.scheduler_run_id) },
          { label: t("scheduler"), key: "schedule_id" },
          { label: t("status"), html: (row) => renderStatusBadge(row.status) },
          { label: "Task ID", html: (row) => shortIdHtml(row.task_id) },
        ], runs, t("no_scheduler_runs"), { selectable: false })}
      </div>
      <div class="editor-panel">
        ${(selected || state.scheduleDraftMode) ? renderSummaryPanel({
          title: state.scheduleDraftMode ? "New Schedule" : "Schedule Summary",
          subtitle: state.scheduleDraftMode
            ? (defaultSpider
              ? `New schedule draft for spider ${defaultSpider.id}. Save it first, then trigger or run due.`
              : "New schedule draft using the default local spider template. Save it first, then trigger or run due.")
            : `Current schedule: ${shortId(selected.id)}. Type must never render blank.`,
          badges: [
            { label: statusMeta(firstFilled(selected?.status, state.scheduleDraftMode ? "enabled" : "unknown")).label, tone: statusMeta(firstFilled(selected?.status, state.scheduleDraftMode ? "enabled" : "unknown")).tone },
            { label: titleCase(scheduleType), tone: "neutral" },
          ],
          actions: scheduleActions,
          fields: [
            summaryField("Schedule ID", firstFilled(selected?.id, "Draft until save")),
            summaryField("Spider", firstFilled(selected?.spider_id, editableSchedule.spider?.id, "Not selected")),
            summaryField("Schedule type", scheduleType),
            summaryField("Spider type", firstFilled(selected?.spider?.type, editableSchedule.spider?.type, selected?.type, "unknown")),
            summaryField("Status", statusMeta(firstFilled(selected?.status, "enabled")).label),
            summaryField("Next run", formatDateTime(firstFilled(selected?.next_run_at, editableSchedule.spider?.scheduler?.start_at))),
            summaryField("Last run", formatDateTime(selected?.last_run_at)),
            summaryField("Warnings", (selected?.warnings || []).join("; ") || "None"),
          ],
          raw: selected || editableSchedule,
          rawLabel: "Show raw schedule JSON / 查看原始 JSON",
        }) : renderEmptyState("Select a schedule", "Choose a schedule on the left, or create a new schedule from an existing spider.")}
        <details class="raw-json-collapsible">
          <summary>Edit JSON / Raw config</summary>
          <div class="details-body">
            ${!defaultSpider && state.scheduleDraftMode ? `<p class="helper-text">No saved spider was found, so this draft starts from the local default spider template.</p>` : ""}
            <textarea id="scheduleEditor" spellcheck="false">${escapeHtml(formatJson(editableSchedule))}</textarea>
          </div>
        </details>
      </div>
    </section>
  `;
}

async function renderWorker() {
  const jobs = (await apiGet("/worker/jobs")).data || [];
  const stats = await safeData("/worker/stats");
  const deadLetters = await safeData("/worker/dead-letters");
  const defaultSpider = await firstAvailableSpiderConfig();
  const defaultJob = buildWorkerJobPayload(defaultSpider);
  if (reviewEmptyMode()) {
    state.selectedJob = null;
  } else if (!state.selectedJob && jobs[0]) {
    state.selectedJob = jobs[0].id || jobs[0].job_id;
  }
  const selectedId = state.selectedJob;
  const detail = selectedId ? await safeData(`/worker/jobs/${encodeURIComponent(selectedId)}`) : null;
  const events = selectedId ? await safeData(`/worker/jobs/${encodeURIComponent(selectedId)}/events`) : null;
  const workerStats = stats || {};
  const workerCounts = workerStats.counts || {};
  const deadLetterRows = Array.isArray(deadLetters) ? deadLetters : [];
  const jobResultRows = detail?.metadata?.result ? [detail.metadata.result] : [];
  view.innerHTML = `
    <div class="view-header"><h2>${t("worker")}</h2>${renderToolbar([{ label: t("run_once"), action: "worker-run-once" }, { label: t("run_until_empty"), action: "worker-run-until-empty" }, { label: t("recover"), action: "worker-recover" }, { label: t("refresh"), action: "refresh", primary: true }])}</div>
    <section class="split">
      <div class="panel-stack">
        ${renderTable([
          { label: "Job ID", html: (row) => shortIdHtml(row.id || row.job_id) },
          { label: t("spider"), key: "spider_id" },
          { label: t("status"), html: (row) => renderStatusBadge(row.status) },
          { label: t("source"), key: "source" },
          { label: "Updated", value: (row) => formatDateTime(row.updated_at) },
        ], jobs, t("no_jobs"), { selectedId: state.selectedJob, rowId: (row) => row.id || row.job_id })}
        ${renderSummaryPanel({
          title: "Worker Queue Summary",
          subtitle: "Monitor queue pressure and recoverability here before drilling into a single job.",
          badges: [
            { label: `${jobs.length} jobs listed`, tone: "neutral" },
            { label: `${firstFilled(workerCounts.running, 0)} running`, tone: workerCounts.running ? "running" : "neutral" },
            { label: `${deadLetterRows.length || firstFilled(workerCounts.dead_letter, workerStats.dead_letter_jobs, 0)} dead letters`, tone: (deadLetterRows.length || firstFilled(workerCounts.dead_letter, workerStats.dead_letter_jobs, 0)) ? "warning" : "success" },
          ],
          fields: [
            summaryField("Queued", firstFilled(workerCounts.queued, 0)),
            summaryField("Running", firstFilled(workerCounts.running, 0)),
            summaryField("Succeeded", firstFilled(workerCounts.succeeded, workerStats.succeeded_jobs, 0)),
            summaryField("Failed", firstFilled(workerCounts.failed, workerStats.failed_jobs, 0)),
            summaryField("Due jobs", firstFilled(workerStats.due_jobs, 0)),
            summaryField("Claimed", firstFilled(workerStats.claimed_jobs, 0)),
            summaryField("Workers", firstFilled(workerStats.workers, 0)),
            summaryField("Worker runs", firstFilled(workerStats.worker_runs, 0)),
            summaryField("Heartbeats", firstFilled(workerStats.heartbeat_count, 0)),
            summaryField("Peak concurrency", firstFilled(workerStats.concurrency_peak, 0)),
          ],
          raw: { stats: workerStats, dead_letters: deadLetterRows },
          rawLabel: "Show raw worker queue JSON / 查看 Worker 队列原始 JSON",
        })}
        <section class="subsection">
          <h3>Dead-letter queue</h3>
          ${deadLetterRows.length
            ? renderTable([
              { label: "Job ID", html: (row) => shortIdHtml(row.job_id || row.id) },
              { label: "Spider", key: "spider_id" },
              { label: "Status", html: (row) => renderStatusBadge(row.status) },
            ], deadLetterRows, "No dead letters", { selectable: false })
            : renderEmptyState("No dead letters", "Recovered and failed jobs will appear here if they cannot be reprocessed.")}
        </section>
      </div>
      <div class="panel-stack">
        ${detail ? renderSummaryPanel({
          title: "Worker Job Summary",
          subtitle: `Current job: ${shortId(selectedId)}. Actions below apply to the selected worker job.`,
          badges: [
            { label: statusMeta(detail.status).label, tone: statusMeta(detail.status).tone },
            { label: titleCase(firstFilled(detail.job_type, "job")), tone: "neutral" },
          ],
          actions: renderToolbar([
            { label: t("pause"), action: "pause-job" },
            { label: t("resume"), action: "resume-job" },
            { label: t("cancel"), action: "cancel-job" },
            { label: t("retry"), action: "retry-job", primary: true },
          ]),
          fields: [
            htmlSummaryField("Job ID", shortIdHtml(firstFilled(detail.job_id, detail.id)), firstFilled(detail.job_id, detail.id)),
            summaryField("Spider", detail.spider_id),
            summaryField("Task ID", firstFilled(detail.task_id, detail.metadata?.result?.task_id, "Not linked")),
            summaryField("Source", firstFilled(detail.source, "manual")),
            summaryField("Status", statusMeta(detail.status).label),
            summaryField("Attempt", `${firstFilled(detail.attempt, 0)} / ${firstFilled(detail.max_attempts, 0)}`),
            summaryField("Saved records", firstFilled(detail.metadata?.result?.saved_records, 0)),
            summaryField("Requests success", firstFilled(detail.metadata?.result?.success_requests, 0)),
            summaryField("Requests failed", firstFilled(detail.metadata?.result?.failed_requests, 0)),
            summaryField("Created", formatDateTime(detail.created_at)),
            summaryField("Started", formatDateTime(detail.started_at)),
            summaryField("Finished", formatDateTime(detail.finished_at)),
          ],
          raw: { detail, events },
          rawLabel: "Show raw worker job JSON / 查看 Worker 作业原始 JSON",
        }) : renderEmptyState("Select a worker job", "Choose a worker job on the left to inspect its summary and result counters.")}
        ${renderPreviewTable("Job result preview", jobResultRows, "This worker job has not produced a stored result summary yet.", ["task_id", "task_status", "total_requests", "success_requests", "failed_requests", "saved_records"])}
        ${renderPreviewTable("Job event preview", events || [], "No worker lifecycle events are recorded for this job yet.", ["created_at", "event_type", "status", "message", "attempt"])}
        <details class="raw-json-collapsible">
          <summary>Queue a new job JSON</summary>
          <div class="details-body">
            ${defaultSpider
              ? `<p class="helper-text">The default payload points at spider ${escapeHtml(defaultSpider.id)} so Enqueue can succeed without guessing a spider_id.</p>`
              : `<div class="empty-state"><strong>Create or save a spider first</strong><p>Please create a spider or save one from Examples before queueing a worker job.</p></div>
                 <div class="inline-actions">
                   <button type="button" data-action="open-spiders">${t("spiders")}</button>
                   <button type="button" data-action="open-examples">${t("examples")}</button>
                 </div>`}
            <div class="inline-actions">
              <button type="button" data-action="format-job">${t("format")}</button>
              <button type="button" data-action="enqueue-job" class="primary">${t("enqueue")}</button>
            </div>
            <textarea id="jobEditor" spellcheck="false">${escapeHtml(formatJson(defaultJob))}</textarea>
          </div>
        </details>
      </div>
    </section>
  `;
}

async function renderStorage() {
  const health = (await apiGet("/storage/health")).data;
  const snapshots = (await apiGet("/storage/snapshots")).data || [];
  const stats = health?.stats || {};
  const storageRows = inventoryRows(stats, [
    "spiders",
    "tasks",
    "result_files",
    "schedules",
    "queue_jobs",
    "session_profiles",
    "observability_logs",
    "export_manifests",
    "snapshots",
  ]);
  view.innerHTML = `
    <div class="view-header"><h2>${t("storage")}</h2>${renderToolbar([{ label: t("repair_dry_run"), action: "storage-repair" }, { label: t("create_snapshot"), action: "storage-snapshot" }, { label: t("refresh"), action: "refresh", primary: true }])}</div>
    <section class="split">
      <div class="panel-stack">
        ${renderSummaryPanel({
          title: "Storage Summary",
          subtitle: "Check health and collection counts here before opening raw storage diagnostics.",
          badges: [
            { label: health?.ok ? "Healthy" : "Needs attention", tone: health?.ok ? "success" : "warning" },
            { label: `${(health?.warnings || []).length} warnings`, tone: (health?.warnings || []).length ? "warning" : "success" },
            { label: `${(health?.errors || []).length} errors`, tone: (health?.errors || []).length ? "failed" : "success" },
          ],
          fields: [
            summaryField("Spiders", firstFilled(stats.spiders, 0)),
            summaryField("Tasks", firstFilled(stats.tasks, 0)),
            summaryField("Result files", firstFilled(stats.result_files, 0)),
            summaryField("Schedules", firstFilled(stats.schedules, 0)),
            summaryField("Queue jobs", firstFilled(stats.queue_jobs, 0)),
            summaryField("Session profiles", firstFilled(stats.session_profiles, 0)),
            summaryField("Logs", firstFilled(stats.observability_logs, 0)),
            summaryField("Exports", firstFilled(stats.export_manifests, 0)),
            summaryField("Snapshots", firstFilled(stats.snapshots, 0)),
          ],
          raw: health,
          rawLabel: "Show raw storage health JSON / 查看存储健康原始 JSON",
        })}
        <section class="subsection">
          <h3>Storage inventory</h3>
          ${storageRows.length
            ? renderTable([
              { label: "Collection", key: "label" },
              { label: "Count", value: (row) => displayText(row.value, "0") },
            ], storageRows, "No storage counts available", { selectable: false, rowId: (row) => row.key })
            : renderEmptyState("No storage counts available", "The storage health endpoint did not return collection counts.")}
        </section>
      </div>
      <div class="panel-stack">
        ${renderSummaryPanel({
          title: "Snapshot Recovery",
          subtitle: "Create a snapshot before testing restore. Dry-run restore stays available from this page.",
          badges: [
            { label: `${snapshots.length} snapshots`, tone: snapshots.length ? "running" : "neutral" },
          ],
          fields: [
            summaryField("Snapshots", snapshots.length),
            summaryField("Temporary files", firstFilled(stats.tmp_files, 0)),
            summaryField("Hash files", firstFilled(stats.hash_files, 0)),
            summaryField("Checkpoints", firstFilled(stats.checkpoints, 0)),
            summaryField("Session events", firstFilled(stats.session_events, 0)),
            summaryField("Worker runs", firstFilled(stats.worker_runs, 0)),
          ],
        })}
        <section class="subsection">
          <h3>Snapshots</h3>
          ${snapshots.length
            ? renderTable([
              { label: t("snapshot"), key: "snapshot_id" },
              { label: t("name"), key: "name" },
              { label: t("created"), value: (row) => formatDateTime(row.created_at) },
            ], snapshots, t("no_snapshots"), { selectable: false })
            : renderEmptyState("No snapshots yet", "Create a storage snapshot before running a dry-run restore.")}
          <div class="compact-input-row">
            <input id="snapshotId" placeholder="${escapeHtml(t("snapshot_placeholder"))}">
            <button type="button" data-action="restore-snapshot">${t("restore_dry_run")}</button>
          </div>
        </section>
      </div>
    </section>
  `;
}

async function renderSessions() {
  const sessions = (await apiGet("/sessions")).data || [];
  const events = (await apiGet("/sessions/events").catch(() => ({ data: [] }))).data || [];
  if (reviewEmptyMode()) {
    state.selectedSession = null;
  } else if (!state.selectedSession && sessions[0]) {
    state.selectedSession = sessions[0].profile_id || sessions[0].id;
  }
  const selectedId = state.selectedSession;
  const detail = selectedId ? await safeData(`/sessions/${encodeURIComponent(selectedId)}`) : null;
  const detailProfile = detail?.profile || null;
  const sessionEvents = selectedId
    ? events.filter((row) => firstFilled(row.profile_id, row.id) === selectedId)
    : [];
  const sessionEventRows = sessionEvents.map((row) => ({
    ...row,
    recorded_at: formatDateTime(row.created_at),
    metadata_keys: previewList(Object.keys(row.metadata || {})),
  }));
  view.innerHTML = `
    <div class="view-header"><h2>${t("sessions")}</h2>${renderToolbar([{ label: t("refresh"), action: "refresh", primary: true }])}</div>
    <section class="split">
      <div class="panel-stack">
        ${renderTable([
          { label: "Profile", html: (row) => shortIdHtml(row.profile_id || row.id) },
          { label: "Created", value: (row) => formatDateTime(row.created_at) },
          { label: t("updated"), value: (row) => formatDateTime(row.updated_at) },
        ], sessions, t("no_sessions"), { selectedId: state.selectedSession })}
        ${renderSummaryPanel({
          title: "Session Store",
          subtitle: "Profiles stay on the left; the selected profile summary and its activity stay on the right.",
          badges: [
            { label: `${sessions.length} profiles`, tone: "neutral" },
            { label: `${events.length} events`, tone: events.length ? "running" : "neutral" },
          ],
          fields: [
            summaryField("Profiles", sessions.length),
            summaryField("Recent events", events.length),
            summaryField("Cookie-backed sessions", events.filter((row) => row.metadata?.cookies || row.metadata?.headers?.["Set-Cookie"]).length),
            summaryField("Storage state captures", events.filter((row) => row.metadata?.storage_state).length),
          ],
          raw: { sessions, events },
          rawLabel: "Show raw session registry JSON / 查看会话注册表原始 JSON",
        })}
      </div>
      <div class="panel-stack">
        ${detailProfile ? renderSummaryPanel({
          title: "Session Summary",
          subtitle: `Current session: ${selectedId}. Cookies and storage state stay redacted until you open raw JSON.`,
          badges: [
            { label: detail?.cookies ? "Cookies available" : "No cookies", tone: detail?.cookies ? "success" : "neutral" },
            { label: detail?.storage_state ? "Storage state saved" : "No storage state", tone: detail?.storage_state ? "running" : "neutral" },
          ],
          actions: renderToolbar([
            { label: t("clear"), action: "clear-session" },
            { label: t("delete"), action: "delete-session", primary: true },
          ]),
          fields: [
            htmlSummaryField("Profile ID", shortIdHtml(detailProfile.profile_id), detailProfile.profile_id),
            summaryField("Account ref", firstFilled(detailProfile.account_ref, "Not linked")),
            summaryField("Created", formatDateTime(detailProfile.created_at)),
            summaryField("Updated", formatDateTime(detailProfile.updated_at)),
            summaryField("Header overrides", countKeys(detailProfile.headers)),
            summaryField("Metadata entries", countKeys(detailProfile.metadata)),
            summaryField("Recent events", sessionEventRows.length),
            summaryField("Cookies", detail?.cookies ? "Stored (redacted)" : "Not stored"),
            summaryField("Storage state", detail?.storage_state ? "Stored" : "Not stored"),
          ],
          raw: detail,
          rawLabel: "Show raw session JSON / 查看会话原始 JSON",
        }) : renderEmptyState("Select a session", "Choose a session profile on the left to inspect its current state and recent activity.")}
        ${renderPreviewTable("Session event preview", sessionEventRows, "No session events are recorded for this profile yet.", ["recorded_at", "event_type", "task_id", "spider_id", "metadata_keys"])}
      </div>
    </section>
  `;
}

async function renderObservability() {
  const logsResponse = await apiGet("/observability/logs", state.pages.logs);
  const logs = logsResponse.data || [];
  const metrics = await safeData("/observability/metrics");
  const logRows = observabilityLogRows(logs);
  if (reviewEmptyMode()) {
    state.selectedLog = null;
  } else if (!state.selectedLog && logRows[0]) {
    state.selectedLog = observabilityRowId(logRows[0]);
  }
  const selectedLog = logRows.find((row) => observabilityRowId(row) === state.selectedLog) || null;
  const currentResult = state.observabilityResult || buildObservabilityResult("/observability/metrics", metrics);
  const inputs = state.observabilityInputs || {};
  view.innerHTML = `
    <div class="view-header"><h2>${t("observability")}</h2>${renderToolbar([{ label: t("refresh"), action: "refresh", primary: true }])}</div>
    <section class="filters">
      <label class="filter-field">
        <span>Task ID</span>
        <input id="reportTaskId" placeholder="${escapeHtml(t("task_id_placeholder"))}" value="${escapeHtml(inputs.reportTaskId || "")}">
      </label>
      <label class="filter-field">
        <span>Job ID</span>
        <input id="reportJobId" placeholder="${escapeHtml(t("job_id_placeholder"))}" value="${escapeHtml(inputs.reportJobId || "")}">
      </label>
      <label class="filter-field">
        <span>Scheduler Run ID</span>
        <input id="reportSchedulerRunId" placeholder="${escapeHtml(t("scheduler_run_id_placeholder"))}" value="${escapeHtml(inputs.reportSchedulerRunId || "")}">
      </label>
      <label class="filter-field">
        <span>Trace ID</span>
        <input id="traceId" placeholder="${escapeHtml(t("trace_id_placeholder"))}" value="${escapeHtml(inputs.traceId || "")}">
      </label>
      <button type="button" data-action="load-task-report">${t("task_report")}</button>
      <button type="button" data-action="load-job-report">${t("job_report")}</button>
      <button type="button" data-action="load-scheduler-report">${t("scheduler_report")}</button>
      <button type="button" data-action="load-trace">${t("trace")}</button>
    </section>
    <section class="split">
      <div>
        ${renderTable([
          { label: "Time", key: "logged_at" },
          { label: t("level"), html: (row) => renderLevelBadge(row.level) },
          { label: t("scope"), key: "source_label" },
          { label: t("target"), html: (row) => shortIdHtml(row.target_id) },
          { label: t("message"), key: "message" },
        ], logRows, t("no_logs"), { selectedId: state.selectedLog, rowId: observabilityRowId })}
        ${renderPager("logs", logsResponse.meta?.pagination, pagerLabels())}
      </div>
      <div>
        ${selectedLog ? renderSummaryPanel({
          title: "Selected Log",
          subtitle: "The selected row stays highlighted while you inspect reports and traces.",
          badges: [{ label: levelMeta(selectedLog.level).label, tone: levelMeta(selectedLog.level).tone }],
          fields: [
            summaryField("Time", selectedLog.logged_at),
            summaryField("Scope", selectedLog.source_label),
            htmlSummaryField("Target", shortIdHtml(selectedLog.target_id), selectedLog.target_id || ""),
            summaryField("Event", firstFilled(selectedLog.event_type, selectedLog.component, "log")),
            summaryField("Task ID", shortId(selectedLog.task_id), selectedLog.task_id || ""),
            summaryField("Job ID", shortId(selectedLog.job_id), selectedLog.job_id || ""),
            summaryField("Scheduler run", shortId(selectedLog.scheduler_run_id), selectedLog.scheduler_run_id || ""),
            summaryField("Trace ID", shortId(selectedLog.trace_id), selectedLog.trace_id || ""),
            summaryField("Request ID", shortId(selectedLog.request_id), selectedLog.request_id || ""),
            summaryField("URL", selectedLog.url),
          ],
          raw: selectedLog,
          rawLabel: "Show raw log JSON / 查看原始日志 JSON",
        }) : renderEmptyState("No log selected", "Pick a log row on the left to inspect its context before opening raw JSON.")}
        <div id="observabilityOutput">${renderObservabilityPayload(currentResult)}</div>
      </div>
    </section>
  `;
}

async function renderExports() {
  const exportsList = (await apiGet("/exports")).data || [];
  if (reviewEmptyMode()) {
    state.selectedExport = null;
  } else if (!state.selectedExport && exportsList[0]) {
    state.selectedExport = exportsList[0].export_id || exportsList[0].id;
  }
  const selectedId = state.selectedExport;
  const detail = selectedId ? await safeData(`/exports/${encodeURIComponent(selectedId)}`) : null;
  const inputs = state.exportInputs || {};
  view.innerHTML = `
    <div class="view-header"><h2>${t("exports")}</h2>${renderToolbar([{ label: t("refresh"), action: "refresh", primary: true }])}</div>
    <section class="filters">
      <label class="filter-field">
        <span>Task ID</span>
        <input id="exportTaskId" placeholder="${escapeHtml(t("task_id_placeholder"))}" value="${escapeHtml(inputs.exportTaskId || "")}">
      </label>
      <label class="filter-field">
        <span>Job ID</span>
        <input id="exportJobId" placeholder="${escapeHtml(t("job_id_placeholder"))}" value="${escapeHtml(inputs.exportJobId || "")}">
      </label>
      <label class="filter-field">
        <span>Scheduler Run ID</span>
        <input id="exportSchedulerRunId" placeholder="${escapeHtml(t("scheduler_run_id_placeholder"))}" value="${escapeHtml(inputs.exportSchedulerRunId || "")}">
      </label>
      <button type="button" data-action="create-task-export">${t("task_export")}</button>
      <button type="button" data-action="create-job-export">${t("job_export")}</button>
      <button type="button" data-action="create-scheduler-export">${t("scheduler_export")}</button>
      <button type="button" data-action="create-logs-export">${t("logs_export")}</button>
    </section>
    <section class="split">
      <div>${renderTable([
        { label: "Export ID", html: (row) => shortIdHtml(row.export_id || row.id) },
        { label: "Source", value: (row) => humanizeFieldName(row.source_type || row.kind) },
        { label: t("status"), html: (row) => renderStatusBadge(row.status) },
        { label: t("format"), value: (row) => String(row.format || "unknown").toUpperCase() },
        { label: t("created"), value: (row) => formatDateTime(row.created_at) },
      ], exportsList, t("no_exports"), { selectedId: state.selectedExport })}</div>
      <div>
        ${renderExportSummary(detail)}
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
  const validation = await apiPost("/spiders/validate", result.data);
  if (!validation.data.valid) {
    throw new Error("Saved example spider did not validate cleanly.");
  }
  state.selectedSpider = result.data.id;
  state.spiderDraftMode = false;
  return result.data;
}

async function runSelectedExample() {
  const spider = await saveSelectedExampleSpider();
  return launchTaskForSpider(spider);
}

async function latestTaskRecord() {
  const response = await apiGet("/tasks", { limit: 1, offset: 0 });
  return (response.data || [])[0] || null;
}

function rememberObservabilityInputs() {
  state.observabilityInputs = {
    reportTaskId: document.getElementById("reportTaskId")?.value || state.observabilityInputs.reportTaskId || "",
    reportJobId: document.getElementById("reportJobId")?.value || state.observabilityInputs.reportJobId || "",
    reportSchedulerRunId: document.getElementById("reportSchedulerRunId")?.value || state.observabilityInputs.reportSchedulerRunId || "",
    traceId: document.getElementById("traceId")?.value || state.observabilityInputs.traceId || "",
  };
}

function rememberExportInputs() {
  state.exportInputs = {
    exportTaskId: document.getElementById("exportTaskId")?.value || state.exportInputs.exportTaskId || "",
    exportJobId: document.getElementById("exportJobId")?.value || state.exportInputs.exportJobId || "",
    exportSchedulerRunId: document.getElementById("exportSchedulerRunId")?.value || state.exportInputs.exportSchedulerRunId || "",
  };
}

function requiredInputValue(id, label, group = "observability") {
  const value = String(document.getElementById(id)?.value || "").trim();
  if (!value) {
    throw new Error(`${label} is required`);
  }
  if (group === "exports") {
    state.exportInputs = { ...state.exportInputs, [id]: value };
  } else {
    state.observabilityInputs = { ...state.observabilityInputs, [id]: value };
  }
  return value;
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
    if (action === "run-local-api-example") {
      state.selectedExample = "local-api-json";
      await runSelectedExample();
      return renderDashboard();
    }
    if (action === "open-latest-task") {
      const latest = await latestTaskRecord();
      if (!latest?.id) {
        throw new Error("Run a task first to open it here.");
      }
      state.selectedTask = latest.id;
      return setActiveView("tasks");
    }
    if (action === "export-latest-task") {
      const latest = await latestTaskRecord();
      if (!latest?.id) {
        throw new Error("Run a task first to export it.");
      }
      state.selectedTask = latest.id;
      const result = await apiPost(`/exports/tasks/${encodeURIComponent(latest.id)}`, { format: "jsonl" });
      state.selectedExport = result.data.export_id || result.data.id || null;
      showToast(t("export_created"), "success");
      return setActiveView("exports");
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
      state.spiderDraftMode = true;
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
      document.getElementById("spiderCanonical").innerHTML = renderRawJsonSection(result.data, "Validation output JSON / 查看校验输出 JSON", true);
      return;
    }
    if (action === "save-spider") {
      const payload = readSpiderEditorConfig();
      const result = state.selectedSpider && !state.spiderDraftMode
        ? await apiPut(`/spiders/${encodeURIComponent(state.selectedSpider)}`, payload)
        : await apiPost("/spiders", payload);
      state.selectedSpider = result.data.id;
      state.spiderDraftMode = false;
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
      await launchTaskForSpider(payload);
      return setActiveView("tasks");
    }
    if (action === "run-spider-with-start-url") {
      const startUrl = readStartUrlInput();
      if (!startUrl) {
        throw new Error(t("start_url_required"));
      }
      const payload = applyStartUrl(readSpiderEditorConfig(), startUrl);
      writeSpiderEditorConfig(payload);
      await launchTaskForSpider(payload, { startUrl });
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
    if (action === "open-task-report") {
      const id = selectedId("selectedTask");
      if (!id) {
        return;
      }
      state.observabilityInputs.reportTaskId = id;
      state.observabilityResult = await loadTaskReportById(id);
      return setActiveView("observability");
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
    if (action === "new-schedule") {
      state.selectedSchedule = null;
      state.scheduleDraftMode = true;
      return renderScheduler();
    }
    if (action === "save-schedule") {
      const payload = parseJsonEditor(document.getElementById("scheduleEditor").value, defaultScheduleConfig);
      const result = await apiPost("/scheduler/schedules", payload);
      state.selectedSchedule = result.data.schedule?.id || result.data.spider_id || payload.spider?.id || null;
      state.scheduleDraftMode = false;
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
      let payload = parseJsonEditor(document.getElementById("jobEditor").value, defaultWorkerJob);
      if (!payload.spider_id && !payload.spider) {
        const fallbackSpider = await firstAvailableSpiderConfig();
        if (!fallbackSpider?.id) {
          throw new Error("Create or save a spider first before queueing a worker job.");
        }
        payload = { ...payload, spider_id: fallbackSpider.id };
      }
      const result = await apiPost("/worker/jobs", payload);
      state.selectedJob = result.data.job_id || result.data.id || null;
      showToast(`Queued job ${shortId(firstFilled(result.data.job_id, result.data.id))}`, "success");
      return renderWorker();
    }
    if (action === "worker-run-once") {
      const result = await apiPost("/worker/run-once", {});
      state.selectedJob = result.data.job_id || state.selectedJob;
      state.selectedTask = result.data.task_id || state.selectedTask;
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
    if (action === "load-task-report") {
      rememberObservabilityInputs();
      const taskId = requiredInputValue("reportTaskId", "Task ID");
      state.observabilityResult = await loadTaskReportById(taskId);
      return renderObservability();
    }
    if (action === "load-job-report") {
      rememberObservabilityInputs();
      return loadObservability(`/observability/reports/jobs/${encodeURIComponent(requiredInputValue("reportJobId", "Job ID"))}`);
    }
    if (action === "load-scheduler-report") {
      rememberObservabilityInputs();
      return loadObservability(`/observability/reports/scheduler/${encodeURIComponent(requiredInputValue("reportSchedulerRunId", "Scheduler Run ID"))}`);
    }
    if (action === "load-trace") {
      rememberObservabilityInputs();
      return loadObservability(`/observability/traces/${encodeURIComponent(requiredInputValue("traceId", "Trace ID"))}`);
    }
    if (action === "create-task-export") {
      rememberExportInputs();
      const result = await apiPost(`/exports/tasks/${encodeURIComponent(requiredInputValue("exportTaskId", "Task ID", "exports"))}`, { format: "jsonl" });
      state.selectedExport = result.data.export_id || result.data.id || null;
      showToast(t("export_created"), "success");
      return renderExports();
    }
    if (action === "create-job-export") {
      rememberExportInputs();
      const result = await apiPost(`/exports/jobs/${encodeURIComponent(requiredInputValue("exportJobId", "Job ID", "exports"))}`, { format: "jsonl" });
      state.selectedExport = result.data.export_id || result.data.id || null;
      showToast(t("export_created"), "success");
      return renderExports();
    }
    if (action === "create-scheduler-export") {
      rememberExportInputs();
      const result = await apiPost(`/exports/scheduler/${encodeURIComponent(requiredInputValue("exportSchedulerRunId", "Scheduler Run ID", "exports"))}`, { format: "jsonl" });
      state.selectedExport = result.data.export_id || result.data.id || null;
      showToast(t("export_created"), "success");
      return renderExports();
    }
    if (action === "create-logs-export") {
      rememberExportInputs();
      const result = await apiPost("/exports/observability/logs", { format: "jsonl" });
      state.selectedExport = result.data.export_id || result.data.id || null;
      showToast(t("export_created"), "success");
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
  if (output) {
    state.observabilityResult = await fetchObservabilityResult(path, { title: "Observability result" });
    output.innerHTML = renderObservabilityPayload(state.observabilityResult);
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
  if (state.activeView === "spiders") {
    state.selectedSpider = id;
    state.spiderDraftMode = false;
  }
  if (state.activeView === "tasks") state.selectedTask = id;
  if (state.activeView === "scheduler") {
    state.selectedSchedule = id;
    state.scheduleDraftMode = false;
  }
  if (state.activeView === "worker") state.selectedJob = id;
  if (state.activeView === "sessions") state.selectedSession = id;
  if (state.activeView === "exports") state.selectedExport = id;
  if (state.activeView === "observability") state.selectedLog = id;
  render();
});

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => setActiveView(button.dataset.view));
});

refreshButton.addEventListener("click", () => render());
languageToggleButton.addEventListener("click", () => toggleLocale());

updateStaticText();
refreshRuntimeSummary();
syncActiveNav();
render();

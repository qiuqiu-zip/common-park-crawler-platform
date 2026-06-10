from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .config_loader import load_spider_config, validate_spider_config
from .crawl_policy import build_crawl_plan
from .debugging import debug_extract, debug_selector, run_dry_run
from .doctor import run_doctor
from .engine import CrawlerEngine
from .examples import copy_example, get_example, list_examples, smoke_examples, validate_examples
from .exporter import ExportError, ExportService
from .lifecycle import InvalidLifecycleTransitionError, TaskLifecycleService
from .scheduler import SchedulerError, SchedulerService
from .storage import FileStore, StorageError
from .validation import SpiderConfigValidationError, ValidationIssue, write_spider_config_schema
from .worker import WorkerService
from .url_seed import inspect_seed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crawler-platform")
    parser.add_argument("--data-dir", default="data", help="File storage root.")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="Run a spider config.")
    run_parser.add_argument("config", help="Spider JSON/YAML config path.")
    run_parser.add_argument("--task-id", help="Optional task id.")
    run_parser.add_argument("--data-dir", dest="run_data_dir", help="File storage root for this run.")
    run_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    run_parser.add_argument("--dry-run", action="store_true", help="Print a crawl plan without fetching pages.")

    plan_parser = sub.add_parser("plan", help="Preview request scope, policy decisions, and run limits without fetching pages.")
    plan_parser.add_argument("config", help="Spider JSON/YAML config path.")
    plan_parser.add_argument("--data-dir", dest="plan_data_dir", help="File storage root for this plan.")
    plan_parser.add_argument("--json", action="store_true", help="Print JSON output.")

    seed_parser = sub.add_parser("seed", help="Inspect seed source and resolve concrete URLs.")
    seed_sub = seed_parser.add_subparsers(dest="seed_command", required=True)
    seed_inspect_parser = seed_sub.add_parser("inspect", help="Inspect one seed payload.")
    seed_inspect_parser.add_argument("seed", help="Seed payload as JSON (or path to a JSON seed file).")
    seed_inspect_parser.add_argument("--max-urls", type=int, help="Limit number of URLs to return.")
    seed_inspect_parser.add_argument("--json", action="store_true", help="Print JSON output.")

    doctor_parser = sub.add_parser("doctor", help="Run local first-use diagnostics.")
    doctor_parser.add_argument("--data-dir", dest="doctor_data_dir", help="File storage root to check.")
    doctor_parser.add_argument("--json", action="store_true", help="Print JSON output.")

    def add_dry_run_options(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("config", help="Spider JSON/YAML config path.")
        command_parser.add_argument("--data-dir", dest="dry_run_data_dir", help="File storage root for debug output.")
        command_parser.add_argument("--max-pages", type=int, default=1, help="Maximum pages to preview.")
        command_parser.add_argument("--max-records", type=int, default=5, help="Maximum records to extract during preview.")
        command_parser.add_argument("--sample-size", type=int, default=5, help="Maximum sample records and values to print.")
        command_parser.add_argument("--json", action="store_true", help="Print JSON output.")
        command_parser.add_argument("--save-report", action="store_true", help="Save the dry-run report under debug_reports.")

    dry_run_parser = sub.add_parser("dry-run", help="Preview one spider without writing formal crawl results.")
    add_dry_run_options(dry_run_parser)

    debug_parser = sub.add_parser("debug", help="Debug spider configs and extraction behavior.")
    debug_sub = debug_parser.add_subparsers(dest="debug_command", required=True)
    debug_dry_run = debug_sub.add_parser("dry-run", help="Preview one spider and emit diagnostics.")
    add_dry_run_options(debug_dry_run)
    debug_selector_parser = debug_sub.add_parser("selector", help="Test one CSS or XPath selector against a local HTML file.")
    debug_selector_parser.add_argument("input_file", help="Local HTML file path.")
    debug_selector_parser.add_argument("--selector", required=True)
    debug_selector_parser.add_argument("--selector-type", choices=["css", "xpath"], default="css")
    debug_selector_parser.add_argument("--sample-size", type=int, default=5)
    debug_selector_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    debug_extract_parser = debug_sub.add_parser("extract", help="Extract sample records from a local input file.")
    debug_extract_parser.add_argument("config", help="Spider JSON/YAML config path.")
    debug_extract_parser.add_argument("--input-file", required=True, help="Local HTML/JSON input file.")
    debug_extract_parser.add_argument("--single-url", help="Source URL context for URL transforms.")
    debug_extract_parser.add_argument("--sample-size", type=int, default=5)
    debug_extract_parser.add_argument("--json", action="store_true", help="Print JSON output.")

    export_parser = sub.add_parser("export", help="Export records, reports, lifecycle events, and observability data.")
    export_sub = export_parser.add_subparsers(dest="export_command", required=True)

    def add_export_options(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--data-dir", dest="export_data_dir", help="File storage root for exports.")
        command_parser.add_argument("--format", choices=["json", "jsonl", "csv", "xlsx"], default=None)
        command_parser.add_argument("--output", help="Output file or directory.")
        command_parser.add_argument("--include-fields", nargs="*", help="Fields to include, comma-separated or repeated.")
        command_parser.add_argument("--exclude-fields", nargs="*", help="Fields to exclude, comma-separated or repeated.")
        command_parser.add_argument("--flatten", choices=["flatten_dot", "flatten_underscore", "json_string"])
        command_parser.add_argument("--include-metadata", action="store_true")
        command_parser.add_argument("--json", action="store_true", help="Print JSON output.")

    export_task = export_sub.add_parser("task", help="Export task results.")
    export_task.add_argument("task_id")
    add_export_options(export_task)
    export_task.add_argument("--limit", type=int, help="Maximum rows to export from a large result set.")
    export_task.add_argument("--offset", type=int, default=0, help="Rows to skip before exporting.")
    export_job = export_sub.add_parser("job", help="Export worker job results and report.")
    export_job.add_argument("job_id")
    add_export_options(export_job)
    export_scheduler = export_sub.add_parser("scheduler", help="Export one scheduler run report.")
    export_scheduler.add_argument("scheduler_run_id")
    add_export_options(export_scheduler)
    export_lifecycle = export_sub.add_parser("lifecycle", help="Export lifecycle events.")
    export_lifecycle.add_argument("--target-type", choices=["task", "job", "scheduler_run"])
    export_lifecycle.add_argument("--target-id")
    add_export_options(export_lifecycle)
    export_observability = export_sub.add_parser("observability", help="Export observability data.")
    export_observability_sub = export_observability.add_subparsers(dest="export_observability_command", required=True)
    export_logs = export_observability_sub.add_parser("logs", help="Export structured observability logs.")
    export_logs.add_argument("--task-id")
    export_logs.add_argument("--job-id")
    export_logs.add_argument("--schedule-id")
    export_logs.add_argument("--scheduler-run-id")
    export_logs.add_argument("--level")
    add_export_options(export_logs)
    export_list = export_sub.add_parser("list", help="List export manifests.")
    export_list.add_argument("--data-dir", dest="export_data_dir", help="File storage root for exports.")
    export_list.add_argument("--json", action="store_true", help="Print JSON output.")
    export_show = export_sub.add_parser("show", help="Show one export manifest.")
    export_show.add_argument("export_id")
    export_show.add_argument("--data-dir", dest="export_data_dir", help="File storage root for exports.")
    export_show.add_argument("--json", action="store_true", help="Print JSON output.")
    export_delete = export_sub.add_parser("delete", help="Delete one export file and manifest.")
    export_delete.add_argument("export_id")
    export_delete.add_argument("--data-dir", dest="export_data_dir", help="File storage root for exports.")
    export_delete.add_argument("--json", action="store_true", help="Print JSON output.")

    list_parser = sub.add_parser("list", help="List spiders or tasks.")
    list_parser.add_argument("kind", choices=["spiders", "tasks"])
    list_parser.add_argument("--data-dir", dest="list_data_dir", help="File storage root.")

    validate_parser = sub.add_parser("validate", help="Validate spider config files.")
    validate_parser.add_argument("configs", nargs="+", help="Spider JSON/YAML config paths.")
    validate_parser.add_argument("--write-schema", help="Write JSON Schema to this path.")

    examples_parser = sub.add_parser("examples", help="List, inspect, validate, smoke-test, and copy bundled examples.")
    examples_sub = examples_parser.add_subparsers(dest="examples_command", required=True)
    examples_list = examples_sub.add_parser("list", help="List indexed examples and templates.")
    examples_list.add_argument("--json", action="store_true", help="Print JSON output.")
    examples_list.add_argument("--no-templates", action="store_true", help="Hide template entries.")
    examples_list.add_argument("--quickstart", action="store_true", help="Show the recommended first examples.")
    examples_show = examples_sub.add_parser("show", help="Show one indexed example or template.")
    examples_show.add_argument("example_id")
    examples_show.add_argument("--json", action="store_true", help="Print JSON output.")
    examples_validate = examples_sub.add_parser("validate", help="Validate the examples index, examples, fixtures, and templates.")
    examples_validate.add_argument("--json", action="store_true", help="Print JSON output.")
    examples_smoke = examples_sub.add_parser("smoke", help="Run the local-fixture smoke subset.")
    examples_smoke.add_argument("--data-dir", dest="examples_data_dir", default="test-output/feature18-examples", help="File storage root for smoke output.")
    examples_smoke.add_argument("--id", dest="example_ids", action="append", help="Run a specific example id. Can be repeated.")
    examples_smoke.add_argument("--json", action="store_true", help="Print JSON output.")
    examples_copy = examples_sub.add_parser("copy", help="Copy an example or template JSON file.")
    examples_copy.add_argument("example_id")
    examples_copy.add_argument("--to", required=True, help="Destination path.")
    examples_copy.add_argument("--json", action="store_true", help="Print JSON output.")

    init_parser = sub.add_parser("init", help="Create starter spider configs.")
    init_sub = init_parser.add_subparsers(dest="init_command", required=True)
    init_spider = init_sub.add_parser("spider", help="Create a starter spider config from a local template.")
    init_spider.add_argument("--type", choices=["api", "http", "playwright"], default="api", help="Starter template type.")
    init_spider.add_argument("--template", help="Template id or shorthand, for example api_basic.")
    init_spider.add_argument("--output", required=True, help="Destination config path.")
    init_spider.add_argument("--json", action="store_true", help="Print JSON output.")

    storage_parser = sub.add_parser("storage", help="Inspect and maintain file storage.")
    storage_sub = storage_parser.add_subparsers(dest="storage_command", required=True)
    storage_check = storage_sub.add_parser("check", help="Check storage health.")
    storage_check.add_argument("--data-dir", dest="storage_data_dir", help="File storage root.")
    storage_check.add_argument("--json", action="store_true", help="Print JSON output.")
    storage_repair = storage_sub.add_parser("repair", help="Repair storage issues.")
    storage_repair.add_argument("--data-dir", dest="storage_data_dir", help="File storage root.")
    storage_repair.add_argument("--dry-run", dest="dry_run", action="store_true", default=True, help="Preview repair actions.")
    storage_repair.add_argument("--apply", dest="dry_run", action="store_false", help="Apply repair actions.")
    storage_repair.add_argument("--json", action="store_true", help="Print JSON output.")
    storage_snapshot = storage_sub.add_parser("snapshot", help="Manage storage snapshots.")
    snapshot_sub = storage_snapshot.add_subparsers(dest="snapshot_command", required=True)
    snapshot_create = snapshot_sub.add_parser("create", help="Create a storage snapshot.")
    snapshot_create.add_argument("--name")
    snapshot_create.add_argument("--data-dir", dest="storage_data_dir", help="File storage root.")
    snapshot_create.add_argument("--include-results", action="store_true")
    snapshot_create.add_argument("--json", action="store_true", help="Print JSON output.")
    snapshot_list = snapshot_sub.add_parser("list", help="List storage snapshots.")
    snapshot_list.add_argument("--data-dir", dest="storage_data_dir", help="File storage root.")
    snapshot_list.add_argument("--json", action="store_true", help="Print JSON output.")
    snapshot_restore = snapshot_sub.add_parser("restore", help="Restore a storage snapshot.")
    snapshot_restore.add_argument("snapshot_id")
    snapshot_restore.add_argument("--data-dir", dest="storage_data_dir", help="File storage root.")
    snapshot_restore.add_argument("--dry-run", dest="dry_run", action="store_true", default=True, help="Preview restore actions.")
    snapshot_restore.add_argument("--apply", dest="dry_run", action="store_false", help="Apply restore actions.")
    snapshot_restore.add_argument("--json", action="store_true", help="Print JSON output.")

    incremental_parser = sub.add_parser("incremental", help="Inspect incremental crawl state.")
    incremental_sub = incremental_parser.add_subparsers(dest="incremental_command", required=True)
    watermark_parser = incremental_sub.add_parser("watermark", help="Inspect watermarks.")
    watermark_sub = watermark_parser.add_subparsers(dest="watermark_command", required=True)
    watermark_list = watermark_sub.add_parser("list", help="List watermarks.")
    watermark_list.add_argument("--spider-id")
    checkpoint_parser = incremental_sub.add_parser("checkpoint", help="Inspect and resume checkpoints.")
    checkpoint_sub = checkpoint_parser.add_subparsers(dest="checkpoint_command", required=True)
    checkpoint_list = checkpoint_sub.add_parser("list", help="List checkpoints.")
    checkpoint_list.add_argument("--spider-id")
    checkpoint_resume = checkpoint_sub.add_parser("resume", help="Resume a failed or cancelled task from checkpoint.")
    checkpoint_resume.add_argument("task_id")

    session_parser = sub.add_parser("session", help="Inspect and clear saved session state.")
    session_sub = session_parser.add_subparsers(dest="session_command", required=True)

    def add_session_data_dir(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--data-dir", dest="session_data_dir", help="File storage root for session state.")
        command_parser.add_argument("--json", action="store_true", help="Print JSON output.")

    session_list = session_sub.add_parser("list", help="List session profiles.")
    add_session_data_dir(session_list)
    session_show = session_sub.add_parser("show", help="Show one session profile.")
    session_show.add_argument("profile_id")
    add_session_data_dir(session_show)
    session_clear = session_sub.add_parser("clear", help="Clear one session profile, cookies, and storage state.")
    session_clear.add_argument("profile_id")
    add_session_data_dir(session_clear)
    session_events = session_sub.add_parser("events", help="List session events.")
    session_events.add_argument("--profile-id")
    add_session_data_dir(session_events)

    observability_parser = sub.add_parser("observability", help="Inspect structured logs, metrics, reports, and traces.")
    observability_sub = observability_parser.add_subparsers(dest="observability_command", required=True)

    def add_observability_data_dir(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--data-dir", dest="observability_data_dir", help="File storage root for observability state.")
        command_parser.add_argument("--json", action="store_true", help="Print JSON output.")

    observability_logs = observability_sub.add_parser("logs", help="List structured logs.")
    observability_logs.add_argument("--task-id")
    observability_logs.add_argument("--job-id")
    observability_logs.add_argument("--schedule-id")
    observability_logs.add_argument("--scheduler-run-id")
    observability_logs.add_argument("--level")
    observability_logs.add_argument("--limit", type=int)
    observability_logs.add_argument("--offset", type=int, default=0)
    add_observability_data_dir(observability_logs)

    observability_metrics = observability_sub.add_parser("metrics", help="Show metrics summary.")
    observability_metrics.add_argument("--task-id")
    observability_metrics.add_argument("--job-id")
    observability_metrics.add_argument("--schedule-id")
    observability_metrics.add_argument("--scheduler-run-id")
    add_observability_data_dir(observability_metrics)

    observability_report = observability_sub.add_parser("report", help="Show one run report.")
    observability_report_sub = observability_report.add_subparsers(dest="observability_report_type", required=True)
    for report_type in ("task", "job", "scheduler"):
        report_parser = observability_report_sub.add_parser(report_type)
        report_parser.add_argument("target_id")
        add_observability_data_dir(report_parser)

    observability_trace = observability_sub.add_parser("trace", help="Show one trace timeline.")
    observability_trace.add_argument("trace_id")
    add_observability_data_dir(observability_trace)

    task_parser = sub.add_parser("task", help="Manage task lifecycle.")
    task_sub = task_parser.add_subparsers(dest="task_command", required=True)

    task_show = task_sub.add_parser("show", help="Show task state and artifact paths.")
    task_show.add_argument("task_id")
    task_show.add_argument("--paths", action="store_true", help="Include FileStore artifact paths.")
    task_show.add_argument("--data-dir", dest="task_data_dir", help="File storage root for task state.")
    task_show.add_argument("--json", action="store_true", help="Print JSON output.")

    def add_task_data_dir(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--data-dir", dest="task_data_dir", help="File storage root for task lifecycle state.")
        command_parser.add_argument("--json", action="store_true", help="Print JSON output.")
        command_parser.add_argument("--reason", help="Lifecycle event reason.")

    task_pause = task_sub.add_parser("pause", help="Pause a pending task.")
    task_pause.add_argument("task_id")
    add_task_data_dir(task_pause)
    task_resume = task_sub.add_parser("resume", help="Resume a paused task.")
    task_resume.add_argument("task_id")
    add_task_data_dir(task_resume)
    task_cancel = task_sub.add_parser("cancel", help="Cancel a task.")
    task_cancel.add_argument("task_id")
    add_task_data_dir(task_cancel)
    task_retry = task_sub.add_parser("retry", help="Create a retry task from a failed or cancelled task.")
    task_retry.add_argument("task_id")
    add_task_data_dir(task_retry)
    task_rerun = task_sub.add_parser("rerun", help="Create a rerun task from a finished task.")
    task_rerun.add_argument("task_id")
    add_task_data_dir(task_rerun)
    task_events = task_sub.add_parser("events", help="List task lifecycle events.")
    task_events.add_argument("task_id")
    add_task_data_dir(task_events)
    task_lifecycle = task_sub.add_parser("lifecycle", help="Show task lifecycle state, signal, and events.")
    task_lifecycle.add_argument("task_id")
    add_task_data_dir(task_lifecycle)

    scheduler_parser = sub.add_parser("scheduler", help="Manage scheduled spiders.")
    scheduler_sub = scheduler_parser.add_subparsers(dest="scheduler_command", required=True)

    def add_scheduler_data_dir(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--data-dir", dest="scheduler_data_dir", help="File storage root for scheduler state.")
        command_parser.add_argument("--json", action="store_true", help="Print JSON output.")

    scheduler_list = scheduler_sub.add_parser("list", help="List schedules.")
    scheduler_list.add_argument("--spider-id")
    scheduler_list.add_argument("--enabled", action="store_true", help="Only list enabled schedules.")
    add_scheduler_data_dir(scheduler_list)
    scheduler_register = scheduler_sub.add_parser("register", help="Register a spider schedule from a config file.")
    scheduler_register.add_argument("config")
    add_scheduler_data_dir(scheduler_register)
    scheduler_run_due = scheduler_sub.add_parser("run-due", help="Run schedules due at the provided time.")
    scheduler_run_due.add_argument("--now")
    scheduler_run_due.add_argument("--enqueue", action="store_true", help="Enqueue due schedules into worker queue instead of running immediately.")
    add_scheduler_data_dir(scheduler_run_due)
    scheduler_enqueue_due = scheduler_sub.add_parser("enqueue-due", help="Enqueue due schedules into the worker queue.")
    scheduler_enqueue_due.add_argument("--now")
    add_scheduler_data_dir(scheduler_enqueue_due)
    scheduler_trigger = scheduler_sub.add_parser("trigger", help="Trigger one schedule immediately.")
    scheduler_trigger.add_argument("schedule_id")
    add_scheduler_data_dir(scheduler_trigger)
    scheduler_pause = scheduler_sub.add_parser("pause", help="Pause a schedule.")
    scheduler_pause.add_argument("schedule_id")
    add_scheduler_data_dir(scheduler_pause)
    scheduler_resume = scheduler_sub.add_parser("resume", help="Resume a schedule.")
    scheduler_resume.add_argument("schedule_id")
    add_scheduler_data_dir(scheduler_resume)
    scheduler_disable = scheduler_sub.add_parser("disable", help="Disable a schedule.")
    scheduler_disable.add_argument("schedule_id")
    add_scheduler_data_dir(scheduler_disable)
    scheduler_runs = scheduler_sub.add_parser("runs", help="List scheduler run records.")
    scheduler_runs.add_argument("--schedule-id")
    scheduler_runs.add_argument("--spider-id")
    add_scheduler_data_dir(scheduler_runs)

    worker_parser = sub.add_parser("worker", help="Manage worker queue and local workers.")
    worker_sub = worker_parser.add_subparsers(dest="worker_command", required=True)

    def add_worker_data_dir(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--data-dir", dest="worker_data_dir", help="File storage root for worker queue state.")
        command_parser.add_argument("--json", action="store_true", help="Print JSON output.")

    worker_enqueue = worker_sub.add_parser("enqueue", help="Enqueue a spider run job.")
    worker_enqueue.add_argument("config")
    worker_enqueue.add_argument("--source", default="manual", choices=["manual", "scheduler", "api"])
    worker_enqueue.add_argument("--priority", type=int, default=0)
    worker_enqueue.add_argument("--run-after")
    worker_enqueue.add_argument("--max-attempts", type=int, default=1)
    add_worker_data_dir(worker_enqueue)
    worker_run_once = worker_sub.add_parser("run-once", help="Claim and execute one due job.")
    worker_run_once.add_argument("--worker-id")
    add_worker_data_dir(worker_run_once)
    worker_run_until_empty = worker_sub.add_parser("run-until-empty", help="Run due jobs until the queue is empty.")
    worker_run_until_empty.add_argument("--worker-id")
    worker_run_until_empty.add_argument("--max-jobs", type=int)
    worker_run_until_empty.add_argument("--max-concurrent-jobs", type=int, default=1)
    add_worker_data_dir(worker_run_until_empty)
    worker_jobs = worker_sub.add_parser("jobs", help="List worker jobs.")
    worker_jobs.add_argument("--status")
    worker_jobs.add_argument("--source")
    worker_jobs.add_argument("--spider-id")
    add_worker_data_dir(worker_jobs)
    worker_stats = worker_sub.add_parser("stats", help="Show worker queue stats.")
    add_worker_data_dir(worker_stats)
    worker_recover = worker_sub.add_parser("recover", help="Recover expired job leases.")
    worker_recover.add_argument("--now")
    add_worker_data_dir(worker_recover)
    worker_dead_letters = worker_sub.add_parser("dead-letters", help="List dead-lettered worker jobs.")
    add_worker_data_dir(worker_dead_letters)
    worker_cancel = worker_sub.add_parser("cancel", help="Cancel a queued worker job.")
    worker_cancel.add_argument("job_id")
    add_worker_data_dir(worker_cancel)

    worker_job = worker_sub.add_parser("job", help="Manage one worker job lifecycle.")
    worker_job_sub = worker_job.add_subparsers(dest="worker_job_command", required=True)

    def add_worker_job_data_dir(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("job_id")
        command_parser.add_argument("--data-dir", dest="worker_data_dir", help="File storage root for worker queue state.")
        command_parser.add_argument("--json", action="store_true", help="Print JSON output.")
        command_parser.add_argument("--reason", help="Lifecycle event reason.")

    for job_command, help_text in [
        ("pause", "Pause a queued worker job."),
        ("resume", "Resume a paused worker job."),
        ("cancel", "Cancel or request cancellation for a worker job."),
        ("retry", "Retry a failed, cancelled, or dead-lettered worker job."),
        ("rerun", "Create a new queued job from an existing worker job."),
        ("events", "List worker job lifecycle events."),
        ("lifecycle", "Show worker job lifecycle state, signal, and events."),
    ]:
        job_parser = worker_job_sub.add_parser(job_command, help=help_text)
        add_worker_job_data_dir(job_parser)

    args = parser.parse_args(argv)
    store = FileStore(
        getattr(args, "run_data_dir", None)
        or getattr(args, "plan_data_dir", None)
        or getattr(args, "dry_run_data_dir", None)
        or getattr(args, "scheduler_data_dir", None)
        or getattr(args, "worker_data_dir", None)
        or getattr(args, "task_data_dir", None)
        or getattr(args, "session_data_dir", None)
        or getattr(args, "observability_data_dir", None)
        or getattr(args, "export_data_dir", None)
        or getattr(args, "examples_data_dir", None)
        or getattr(args, "doctor_data_dir", None)
        or getattr(args, "list_data_dir", None)
        or getattr(args, "storage_data_dir", None)
        or args.data_dir
    )

    if args.command == "run":
        spider = load_spider_config(args.config)
        if args.dry_run:
            payload = build_crawl_plan(spider)
            _print_plan_payload(payload, args.json)
            return 0 if payload.get("safe_to_run") else 1
        task = CrawlerEngine(store=store).run(spider, task_id=args.task_id)
        payload = {
            "task_id": task.id,
            "status": task.status.value,
            "records_count": task.saved_records or task.saved_count,
            **task.to_dict(),
        }
        _print_run_payload(payload, args.json, store)
        return 0 if task.status.value == "success" else 1

    if args.command == "plan":
        spider = load_spider_config(args.config)
        payload = build_crawl_plan(spider)
        _print_plan_payload(payload, args.json)
        return 0 if payload.get("safe_to_run") else 1

    if args.command == "seed":
        return _handle_seed_command(args)

    if args.command == "doctor":
        payload = run_doctor(store.root)
        _print_doctor_payload(payload, args.json)
        return 0 if payload.get("status") == "passed" else 1

    if args.command == "dry-run":
        return _handle_dry_run_command(args, store)

    if args.command == "debug":
        if args.debug_command == "dry-run":
            return _handle_dry_run_command(args, store)
        if args.debug_command == "selector":
            return _handle_debug_selector_command(args)
        if args.debug_command == "extract":
            return _handle_debug_extract_command(args)
        return 2

    if args.command == "export":
        return _handle_export_command(args, store)

    if args.command == "list":
        payload = store.list_spiders() if args.kind == "spiders" else store.list_tasks()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.command == "validate":
        if args.write_schema:
            write_spider_config_schema(args.write_schema)
        results = []
        ok = True
        for config in args.configs:
            try:
                spider = load_spider_config(config)
                result = validate_spider_config(spider)
            except SpiderConfigValidationError as exc:
                result = exc.result
            except Exception as exc:
                result = validate_spider_config({})
                result.issues.append(ValidationIssue(config, str(exc)))
                result.valid = False
            ok = ok and result.valid
            results.append({"path": config, **result.to_dict()})
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    if args.command == "examples":
        return _handle_examples_command(args)

    if args.command == "init":
        return _handle_init_command(args)

    if args.command == "storage":
        try:
            return _handle_storage_command(args, store)
        except StorageError as exc:
            print(str(exc))
            return 1

    if args.command == "incremental":
        return _handle_incremental_command(args, store)

    if args.command == "session":
        return _handle_session_command(args, store)

    if args.command == "observability":
        return _handle_observability_command(args, store)

    if args.command == "task":
        return _handle_task_command(args, store)

    if args.command == "scheduler":
        return _handle_scheduler_command(args, store)

    if args.command == "worker":
        return _handle_worker_command(args, store)

    parser.error("Unknown command")
    return 2


def _handle_examples_command(args: argparse.Namespace) -> int:
    try:
        if args.examples_command == "list":
            payload = list_examples(include_templates=not args.no_templates, quickstart_only=args.quickstart)
            _print_examples_payload(payload, args.json, kind="list")
            return 0
        if args.examples_command == "show":
            payload = get_example(args.example_id)
            _print_examples_payload(payload, args.json, kind="show")
            return 0
        if args.examples_command == "validate":
            payload = validate_examples()
            _print_examples_payload(payload, args.json, kind="validate")
            return 0 if payload.get("valid") else 1
        if args.examples_command == "smoke":
            payload = smoke_examples(args.examples_data_dir, ids=args.example_ids)
            _print_examples_payload(payload, args.json, kind="smoke")
            return 0 if payload.get("valid") else 1
        if args.examples_command == "copy":
            payload = copy_example(args.example_id, args.to)
            _print_examples_payload(payload, args.json, kind="copy")
            return 0
    except (FileNotFoundError, RuntimeError) as exc:
        print(str(exc))
        return 1
    return 2


def _handle_init_command(args: argparse.Namespace) -> int:
    try:
        if args.init_command == "spider":
            template_id = _resolve_template_id(args.template, args.type)
            payload = copy_example(template_id, args.output)
            spider = load_spider_config(args.output)
            result = validate_spider_config(spider)
            payload = {
                **payload,
                "output": args.output,
                "template_id": template_id,
                "valid": result.valid,
                "issues": [issue.to_dict() for issue in result.issues],
            }
            _print_init_payload(payload, args.json)
            return 0 if result.valid else 1
    except (FileNotFoundError, RuntimeError, SpiderConfigValidationError) as exc:
        print(str(exc))
        return 1
    return 2


def _handle_seed_command(args: argparse.Namespace) -> int:
    try:
        if args.seed_command == "inspect":
            payload = _resolve_seed_input(args.seed)
            result = inspect_seed(payload, max_urls=args.max_urls)
            _print_seed_payload(result, args.json)
            return 0
    except Exception as exc:
        print(str(exc))
        return 1
    return 2


def _resolve_seed_input(raw: str) -> Any:
    value = raw.strip()
    if not value:
        raise ValueError("seed is required")
    path = Path(value)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)


def _handle_dry_run_command(args: argparse.Namespace, store: FileStore) -> int:
    try:
        spider = load_spider_config(args.config)
        report = run_dry_run(
            spider,
            store,
            max_pages=max(1, args.max_pages),
            max_records=max(0, args.max_records),
            sample_size=max(0, args.sample_size),
            save_report=args.save_report,
        )
        _print_dry_run_payload(report.to_dict(), args.json)
        return 1 if report.errors else 0
    except Exception as exc:
        print(str(exc))
        return 1


def _handle_debug_selector_command(args: argparse.Namespace) -> int:
    try:
        payload = debug_selector(args.input_file, selector=args.selector, selector_type=args.selector_type, sample_size=max(0, args.sample_size))
        _print_debug_payload(payload, args.json, kind="selector")
        return 0
    except Exception as exc:
        print(str(exc))
        return 1


def _handle_debug_extract_command(args: argparse.Namespace) -> int:
    try:
        spider = load_spider_config(args.config)
        payload = debug_extract(spider, input_file=args.input_file, sample_size=max(0, args.sample_size), single_url=args.single_url)
        _print_debug_payload(payload, args.json, kind="extract")
        return 0
    except Exception as exc:
        print(str(exc))
        return 1


def _handle_session_command(args: argparse.Namespace, store: FileStore) -> int:
    try:
        if args.session_command == "list":
            _print_session_payload(store.list_session_profiles(), args.json)
            return 0
        if args.session_command == "show":
            profile = store.get_session_profile(args.profile_id)
            cookies = {}
            storage_state = None
            try:
                cookies = store.load_cookies(args.profile_id)
            except FileNotFoundError:
                pass
            try:
                storage_state = store.load_storage_state(args.profile_id)
            except FileNotFoundError:
                pass
            _print_session_payload({"profile": profile, "cookies": cookies, "storage_state": storage_state}, args.json)
            return 0
        if args.session_command == "clear":
            _print_session_payload(store.delete_session_profile(args.profile_id), args.json)
            return 0
        if args.session_command == "events":
            _print_session_payload(store.list_session_events(profile_id=args.profile_id), args.json)
            return 0
    except FileNotFoundError as exc:
        print(str(exc))
        return 1
    return 2


def _handle_observability_command(args: argparse.Namespace, store: FileStore) -> int:
    try:
        if args.observability_command == "logs":
            scope, target_id = _observability_target(args)
            payload = store.iter_logs(scope=scope, target_id=target_id, level=args.level, limit=args.limit, offset=args.offset)
            _print_observability_payload(payload, args.json, kind="logs")
            return 0
        if args.observability_command == "metrics":
            scope, target_id = _observability_target(args)
            payload = store.summarize_metrics(scope=scope, target_id=target_id)
            _print_observability_payload(payload, args.json, kind="metrics")
            return 0
        if args.observability_command == "report":
            payload = store.get_run_report(args.observability_report_type, args.target_id)
            _print_observability_payload(payload, args.json, kind="report")
            return 0
        if args.observability_command == "trace":
            payload = store.get_trace(args.trace_id)
            _print_observability_payload(payload, args.json, kind="trace")
            return 0
    except FileNotFoundError as exc:
        print(str(exc))
        return 1
    except StorageError as exc:
        print(str(exc))
        return 1
    return 2


def _handle_export_command(args: argparse.Namespace, store: FileStore) -> int:
    service = ExportService(store)
    try:
        if args.export_command == "task":
            manifest = service.export_task(args.task_id, fmt=args.format, output=args.output, **_export_cli_options(args))
            _print_export_payload(manifest, args.json)
            return 0
        if args.export_command == "job":
            manifest = service.export_job(args.job_id, fmt=args.format, output=args.output, **_export_cli_options(args))
            _print_export_payload(manifest, args.json)
            return 0
        if args.export_command == "scheduler":
            manifest = service.export_scheduler_run(args.scheduler_run_id, fmt=args.format, output=args.output, **_export_cli_options(args))
            _print_export_payload(manifest, args.json)
            return 0
        if args.export_command == "lifecycle":
            manifest = service.export_lifecycle_events(args.target_type, args.target_id, fmt=args.format, output=args.output, **_export_cli_options(args))
            _print_export_payload(manifest, args.json)
            return 0
        if args.export_command == "observability" and args.export_observability_command == "logs":
            manifest = service.export_observability_logs(
                task_id=args.task_id,
                job_id=args.job_id,
                schedule_id=args.schedule_id,
                scheduler_run_id=args.scheduler_run_id,
                level=args.level,
                fmt=args.format,
                output=args.output,
                **_export_cli_options(args),
            )
            _print_export_payload(manifest, args.json)
            return 0
        if args.export_command == "list":
            _print_export_payload(service.list_exports(), args.json)
            return 0
        if args.export_command == "show":
            _print_export_payload(service.get_export(args.export_id), args.json)
            return 0
        if args.export_command == "delete":
            _print_export_payload(service.delete_export(args.export_id), args.json)
            return 0
    except (ExportError, StorageError, FileNotFoundError) as exc:
        print(str(exc))
        return 1
    return 2


def _handle_scheduler_command(args: argparse.Namespace, store: FileStore) -> int:
    service = SchedulerService(store=store)
    try:
        if args.scheduler_command == "list":
            payload = service.list_schedules(enabled=True if args.enabled else None, spider_id=args.spider_id)
            _print_scheduler_payload(payload, args.json)
            return 0

        if args.scheduler_command == "register":
            spider = load_spider_config(args.config)
            job = service.register_spider_schedule(spider)
            payload = {"registered": job is not None, "schedule": job.to_dict() if job else None, "spider_id": spider.id}
            _print_scheduler_payload(payload, args.json)
            return 0

        if args.scheduler_command == "run-due":
            payload = service.run_due_jobs(now=args.now, enqueue=args.enqueue)
            _print_scheduler_payload(payload, args.json)
            return 0

        if args.scheduler_command == "enqueue-due":
            payload = service.enqueue_due_jobs(now=args.now)
            _print_scheduler_payload(payload, args.json)
            return 0

        if args.scheduler_command == "trigger":
            payload = service.trigger_schedule_now(args.schedule_id)
            _print_scheduler_payload(payload, args.json)
            return 0

        if args.scheduler_command == "pause":
            _print_scheduler_payload(service.pause_schedule(args.schedule_id).to_dict(), args.json)
            return 0

        if args.scheduler_command == "resume":
            _print_scheduler_payload(service.resume_schedule(args.schedule_id).to_dict(), args.json)
            return 0

        if args.scheduler_command == "disable":
            _print_scheduler_payload(service.disable_schedule(args.schedule_id).to_dict(), args.json)
            return 0

        if args.scheduler_command == "runs":
            payload = service.list_scheduler_runs(schedule_id=args.schedule_id, spider_id=args.spider_id)
            _print_scheduler_payload(payload, args.json)
            return 0
    except (FileNotFoundError, SchedulerError) as exc:
        print(str(exc))
        return 1

    return 2


def _handle_worker_command(args: argparse.Namespace, store: FileStore) -> int:
    max_concurrent = getattr(args, "max_concurrent_jobs", 1) or 1
    service = WorkerService(store=store, max_concurrent_jobs=max_concurrent)
    try:
        if args.worker_command == "enqueue":
            spider = load_spider_config(args.config)
            job = service.enqueue_spider_run(
                spider,
                source=args.source,
                priority=args.priority,
                run_after=args.run_after,
                max_attempts=args.max_attempts,
            )
            _print_worker_payload(job.to_dict(), args.json)
            return 0

        if args.worker_command == "run-once":
            _print_worker_payload(service.run_once(worker_id=args.worker_id).to_dict(), args.json)
            return 0

        if args.worker_command == "run-until-empty":
            _print_worker_payload(service.run_until_empty(worker_id=args.worker_id, max_jobs=args.max_jobs), args.json)
            return 0

        if args.worker_command == "jobs":
            payload = store.list_jobs(status=args.status, source=args.source, spider_id=args.spider_id)
            _print_worker_payload(payload, args.json)
            return 0

        if args.worker_command == "stats":
            _print_worker_payload(service.stats(), args.json)
            return 0

        if args.worker_command == "recover":
            _print_worker_payload(service.recover_expired_jobs(now=args.now), args.json)
            return 0

        if args.worker_command == "dead-letters":
            _print_worker_payload(store.list_jobs(status="dead_letter"), args.json)
            return 0

        if args.worker_command == "cancel":
            _print_worker_payload(store.cancel_job(args.job_id).to_dict(), args.json)
            return 0

        if args.worker_command == "job":
            reason = getattr(args, "reason", None)
            if args.worker_job_command == "pause":
                _print_worker_payload(service.pause_job(args.job_id, reason=reason).to_dict(), args.json)
                return 0
            if args.worker_job_command == "resume":
                _print_worker_payload(service.resume_job(args.job_id, reason=reason).to_dict(), args.json)
                return 0
            if args.worker_job_command == "cancel":
                _print_worker_payload(service.cancel_job(args.job_id, reason=reason).to_dict(), args.json)
                return 0
            if args.worker_job_command == "retry":
                _print_worker_payload(service.retry_job(args.job_id, reason=reason).to_dict(), args.json)
                return 0
            if args.worker_job_command == "rerun":
                _print_worker_payload(service.rerun_job(args.job_id, reason=reason).to_dict(), args.json)
                return 0
            if args.worker_job_command == "events":
                _print_worker_payload(service.list_job_events(args.job_id), args.json)
                return 0
            if args.worker_job_command == "lifecycle":
                from .lifecycle import WorkerLifecycleService

                _print_worker_payload(WorkerLifecycleService(store).get_job_lifecycle(args.job_id), args.json)
                return 0
    except (FileNotFoundError, StorageError, RuntimeError, InvalidLifecycleTransitionError) as exc:
        print(str(exc))
        return 1

    return 2


def _handle_task_command(args: argparse.Namespace, store: FileStore) -> int:
    service = TaskLifecycleService(store=store, operator="cli")
    reason = getattr(args, "reason", None)
    try:
        if args.task_command == "show":
            task = store.load_task(args.task_id).to_dict()
            payload = {"task": task}
            if args.paths:
                payload["paths"] = _task_paths(store, args.task_id)
            _print_task_payload(payload, args.json)
            return 0
        if args.task_command == "pause":
            _print_task_payload(service.pause_task(args.task_id, reason=reason).to_dict(), args.json)
            return 0
        if args.task_command == "resume":
            _print_task_payload(service.resume_task(args.task_id, reason=reason).to_dict(), args.json)
            return 0
        if args.task_command == "cancel":
            _print_task_payload(service.cancel_task(args.task_id, reason=reason).to_dict(), args.json)
            return 0
        if args.task_command == "retry":
            _print_task_payload(service.retry_task(args.task_id, reason=reason).to_dict(), args.json)
            return 0
        if args.task_command == "rerun":
            _print_task_payload(service.rerun_task(args.task_id, reason=reason).to_dict(), args.json)
            return 0
        if args.task_command == "events":
            _print_task_payload(service.list_task_events(args.task_id), args.json)
            return 0
        if args.task_command == "lifecycle":
            _print_task_payload(service.get_task_lifecycle(args.task_id), args.json)
            return 0
    except (FileNotFoundError, StorageError, RuntimeError, InvalidLifecycleTransitionError) as exc:
        print(str(exc))
        return 1
    return 2


def _print_run_payload(payload: dict, json_mode: bool, store: FileStore) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    task_id = str(payload.get("task_id") or payload.get("id") or "")
    status = str(payload.get("status") or "unknown")
    paths = _task_paths(store, task_id)
    print(f"Run {status}: {task_id}")
    print(f"Spider: {payload.get('spider_id') or ''}")
    print(f"Records: extracted={payload.get('records_count', 0)} saved={payload.get('saved_records') or payload.get('saved_count') or 0}")
    print(f"Results: {paths['result_jsonl']['path']}")
    print(f"Report: {paths['report']['path']}")
    print(f"Logs: {paths['logs']['path']}")
    print(f"Metrics: {paths['metrics']['path']}")
    print(f"Next export: python -m crawler_platform.cli export task {task_id} --format json --data-dir {store.root}")
    print("Web Admin: python -m uvicorn crawler_platform.api:create_app --factory --reload")


def _print_doctor_payload(payload: dict, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(f"Doctor {payload.get('status')} data_dir={payload.get('data_dir')}")
    for check in payload.get("checks") or []:
        marker = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}.get(check.get("status"), str(check.get("status")).upper())
        print(f"{marker} {check.get('name')}: {check.get('message')}")
        command = (check.get("details") or {}).get("command")
        if command:
            print(f"  command: {command}")
    summary = payload.get("summary") or {}
    print(f"Summary: passed={summary.get('passed', 0)} failed={summary.get('failed', 0)} skipped={summary.get('skipped', 0)}")


def _print_init_payload(payload: dict, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(f"Created spider config: {payload.get('output')}")
    print(f"Template: {payload.get('template_id')}")
    print(f"Valid: {payload.get('valid')}")
    for issue in payload.get("issues") or []:
        print(f"- {issue.get('path')}: {issue.get('message')}")


def _resolve_template_id(template: str | None, spider_type: str) -> str:
    default_ids = {
        "api": "template-api-basic",
        "http": "template-http-basic",
        "playwright": "template-playwright-basic",
    }
    raw = (template or default_ids[spider_type]).strip().replace("_", "-")
    if not raw.startswith("template-"):
        raw = f"template-{raw}"
    return raw


def _task_paths(store: FileStore, task_id: str) -> dict[str, object]:
    name = _safe_name(task_id)
    exports = store.list_exports(source_type="task", source_id=task_id)
    return {
        "task_file": _path_info(store.tasks_dir / f"{name}.json"),
        "result_jsonl": _path_info(store.results_dir / f"{name}.jsonl"),
        "report": _path_info(store.observability_report_tasks_dir / f"{name}.json"),
        "logs": _path_info(store.observability_log_tasks_dir / f"{name}.jsonl"),
        "metrics": _path_info(store.observability_metric_tasks_dir / f"{name}.jsonl"),
        "checkpoint": _path_info(store.checkpoints_dir / f"{name}.json"),
        "lifecycle_events": _path_info(store.lifecycle_task_events_dir / name),
        "lifecycle_signal": _path_info(store.lifecycle_task_signals_dir / f"{name}.json"),
        "exports": {
            "path": str(store.exports_files_dir),
            "exists": store.exports_files_dir.exists(),
            "manifests": [
                {
                    "export_id": item.get("export_id"),
                    "format": item.get("format"),
                    "path": item.get("path"),
                }
                for item in exports
            ],
        },
    }


def _path_info(path: Path) -> dict[str, object]:
    return {"path": str(path), "exists": path.exists()}


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "task"


def _print_examples_payload(payload, json_mode: bool, *, kind: str) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if kind == "list":
        if not payload:
            print("No examples")
            return
        for item in payload:
            labels = []
            if item.get("quickstart"):
                labels.append("quickstart")
            labels.append("template" if item.get("template") else ("runnable" if item.get("runnable") else "reference"))
            suffix = ",".join(labels)
            print(f"{item.get('id')} | {item.get('title')} | {item.get('feature')} | {suffix}")
        return
    if kind == "show":
        print(f"{payload.get('id')} | {payload.get('title')} | {payload.get('feature')}")
        print(f"path={payload.get('path')} runnable={payload.get('runnable')} external={payload.get('requires_external_network')}")
        print(f"tags={','.join(payload.get('tags') or [])}")
        print(f"fixtures={','.join(payload.get('fixture_paths') or [])}")
        if payload.get("expected"):
            print(f"expected={json.dumps(payload.get('expected'), ensure_ascii=False)}")
        return
    if kind == "validate":
        print(f"examples valid={payload.get('valid')} count={payload.get('count')} errors={len(payload.get('errors') or [])}")
        for error in payload.get("errors") or []:
            print(f"- {error.get('path')}: {error.get('message')}")
        return
    if kind == "smoke":
        print(f"examples smoke valid={payload.get('valid')} count={payload.get('count')}")
        for item in payload.get("results") or []:
            print(f"{item.get('id')} {item.get('status')} runner={item.get('runner', '')} records={item.get('records_count', '')}".strip())
        return
    if kind == "copy":
        print(f"Copied {payload.get('id')} -> {payload.get('target')}")
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _print_worker_payload(payload, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if isinstance(payload, list):
        if not payload:
            print("No worker records")
        for item in payload:
            if "event_id" in item:
                print(f"{item.get('event_id')} {item.get('target_id')} {item.get('event_type')} {item.get('from_status') or ''}->{item.get('to_status') or ''}")
            else:
                print(f"{item.get('job_id') or item.get('run_id')} {item.get('spider_id', '')} {item.get('status')} {item.get('source', '')}".strip())
        return
    if isinstance(payload, dict) and "run_id" in payload:
        print(f"{payload['run_id']} {payload.get('job_id') or ''} {payload.get('status')}".strip())
        return
    if isinstance(payload, dict) and "job_id" in payload:
        print(f"{payload['job_id']} {payload.get('spider_id')} {payload.get('status')} priority={payload.get('priority')} source={payload.get('source')}")
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _print_dry_run_payload(payload: dict, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(
        "dry-run "
        f"{payload.get('dry_run_id')} "
        f"target={payload.get('target_url')} "
        f"status={payload.get('status_code')} "
        f"type={payload.get('response_type')} "
        f"items={payload.get('item_count')} "
        f"duration_ms={payload.get('duration_ms')}"
    )
    for warning in payload.get("warnings") or []:
        print(f"warning: {warning}")
    for error in payload.get("errors") or []:
        print(f"error: {error.get('error_type')} {error.get('message')}")
    for field in payload.get("field_diagnostics") or []:
        print(
            "field "
            f"{field.get('field')} "
            f"type={field.get('selector_type')} "
            f"matches={field.get('match_count')} "
            f"missing={field.get('missing_count')} "
            f"required={field.get('required')}"
        )
    for quality in payload.get("field_quality") or []:
        print(
            "quality "
            f"{quality.get('field')} "
            f"status={quality.get('status')} "
            f"missing_rate={quality.get('missing_rate')} "
            f"non_empty={quality.get('non_empty_count')}/{quality.get('total_records')}"
        )
        if quality.get("hint"):
            print(f"  hint={quality.get('hint')}")
    if payload.get("sample_records"):
        print("samples=" + json.dumps(payload.get("sample_records"), ensure_ascii=False))
    if payload.get("report_path"):
        print(f"report={payload.get('report_path')}")
    if payload.get("artifact_path"):
        print(f"artifacts={payload.get('artifact_path')}")


def _print_plan_payload(payload: dict, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(
        "plan "
        f"{payload.get('spider_id')} "
        f"safe_to_run={payload.get('safe_to_run')} "
        f"estimated_max_requests={payload.get('estimated_max_requests')} "
        f"allowed_domains={','.join(payload.get('allowed_domains') or [])}"
    )
    policy = payload.get("crawl_policy") or {}
    print(
        "policy "
        f"enabled={policy.get('enabled')} "
        f"checked={policy.get('policy_checked_urls')} "
        f"blocked={policy.get('policy_blocked_urls')} "
        f"warnings={policy.get('policy_warnings')} "
        f"normalized={policy.get('normalized_urls')}"
    )
    for blocked in payload.get("blocked_urls") or []:
        print(f"blocked: {blocked.get('normalized_url')} {blocked.get('violations')}")
    for warning in payload.get("warnings") or []:
        print(f"warning: {warning}")


def _print_seed_payload(payload: dict, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    source = payload.get("source") or {}
    print(f"seed source_count={payload.get('source_count')}")
    if isinstance(source, dict):
        source_type = source.get("type")
        source_value = source.get("source")
        if source_type:
            print(f"source type={source_type}")
        if source_value:
            print(f"source={source_value}")
    urls = payload.get("urls") or []
    for url in urls:
        print(f"- {url}")


def _print_debug_payload(payload: dict, json_mode: bool, *, kind: str) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if kind == "selector":
        print(
            "selector "
            f"{payload.get('input_file')} "
            f"type={payload.get('selector_type')} "
            f"matches={payload.get('matched_count')}"
        )
        if payload.get("sample_values"):
            print("samples=" + json.dumps(payload.get("sample_values"), ensure_ascii=False))
        return
    print(
        "extract "
        f"{payload.get('spider_id')} "
        f"type={payload.get('response_type')} "
        f"scopes={payload.get('scope_count')} "
        f"samples={len(payload.get('sample_records') or [])}"
    )
    for quality in payload.get("field_quality") or []:
        print(f"quality {quality.get('field')} status={quality.get('status')} missing_rate={quality.get('missing_rate')}")
        if quality.get("hint"):
            print(f"  hint={quality.get('hint')}")
    if payload.get("sample_records"):
        print("samples=" + json.dumps(payload.get("sample_records"), ensure_ascii=False))


def _print_task_payload(payload, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if isinstance(payload, list):
        if not payload:
            print("No task lifecycle events")
        for item in payload:
            print(f"{item.get('event_id')} {item.get('target_id')} {item.get('event_type')} {item.get('from_status') or ''}->{item.get('to_status') or ''}")
        return
    if isinstance(payload, dict) and "task" in payload:
        task = payload["task"]
        print(f"{task.get('id')} {task.get('spider_id')} {task.get('status')} events={len(payload.get('events', []))}")
        paths = payload.get("paths") or {}
        for label, info in paths.items():
            if label == "exports" and isinstance(info, dict):
                print(f"exports {info.get('path')} manifests={len(info.get('manifests') or [])}")
                for manifest in info.get("manifests") or []:
                    print(f"  {manifest.get('export_id')} {manifest.get('format')} {manifest.get('path')}")
                continue
            if isinstance(info, dict):
                exists = "exists" if info.get("exists") else "missing"
                print(f"{label} {info.get('path')} {exists}")
        return
    if isinstance(payload, dict) and "id" in payload:
        print(f"{payload['id']} {payload.get('spider_id')} {payload.get('status')}")
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _print_session_payload(payload, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if isinstance(payload, list):
        if not payload:
            print("No session records")
        for item in payload:
            if "event_id" in item:
                print(f"{item.get('event_id')} {item.get('profile_id')} {item.get('event_type')} {item.get('created_at') or ''}".strip())
            else:
                print(f"{item.get('profile_id')} account={item.get('account_ref') or ''} updated={item.get('updated_at') or ''}".strip())
        return
    if isinstance(payload, dict) and "profile" in payload:
        profile = payload["profile"]
        print(
            f"{profile.get('profile_id')} cookies={len(payload.get('cookies') or {})} "
            f"storage_state={'yes' if payload.get('storage_state') else 'no'} updated={profile.get('updated_at') or ''}"
        )
        return
    if isinstance(payload, dict) and "profile_id" in payload:
        print(f"{payload['profile_id']} removed={len(payload.get('removed', []))}")
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _observability_target(args: argparse.Namespace) -> tuple[str | None, str | None]:
    if getattr(args, "task_id", None):
        return "tasks", args.task_id
    if getattr(args, "job_id", None):
        return "jobs", args.job_id
    if getattr(args, "scheduler_run_id", None):
        return "scheduler", args.scheduler_run_id
    if getattr(args, "schedule_id", None):
        return "scheduler", args.schedule_id
    return None, None


def _print_observability_payload(payload, json_mode: bool, *, kind: str) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return
    if kind == "logs":
        if not payload:
            print("No observability logs")
            return
        for item in payload:
            print(
                f"{item.get('timestamp')} {item.get('level')} {item.get('component')} "
                f"{item.get('event_type')} trace={item.get('trace_id') or ''} {item.get('message') or ''}".strip()
            )
        return
    if kind == "metrics":
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return
    if kind == "trace":
        events = payload.get("events", []) if isinstance(payload, dict) else []
        print(f"Trace {payload.get('trace_id')} events={len(events)}")
        for item in events:
            print(f"{item.get('timestamp')} {item.get('event_type')} {item.get('url') or ''}".strip())
        return
    print(json.dumps(payload, ensure_ascii=True, indent=2))


def _export_cli_options(args: argparse.Namespace) -> dict[str, object]:
    options: dict[str, object] = {}
    include_fields = _split_cli_fields(getattr(args, "include_fields", None))
    exclude_fields = _split_cli_fields(getattr(args, "exclude_fields", None))
    if include_fields is not None:
        options["include_fields"] = include_fields
    if exclude_fields is not None:
        options["exclude_fields"] = exclude_fields
    if getattr(args, "flatten", None):
        options["flatten"] = args.flatten
    if getattr(args, "include_metadata", False):
        options["include_metadata"] = True
    if getattr(args, "limit", None) is not None:
        options["limit"] = max(0, int(args.limit))
    if getattr(args, "offset", None):
        options["offset"] = max(0, int(args.offset))
    return options


def _split_cli_fields(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    fields: list[str] = []
    for value in values:
        fields.extend(item.strip() for item in str(value).split(",") if item.strip())
    return fields


def _print_export_payload(payload, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if isinstance(payload, list):
        if not payload:
            print("No exports")
            return
        for item in payload:
            print(
                f"{item.get('export_id')} {item.get('source_type')} {item.get('format')} "
                f"rows={item.get('rows_count')} status={item.get('status')} {item.get('path')}"
            )
        return
    if isinstance(payload, dict) and "removed" in payload:
        print(f"Deleted export {payload.get('export_id')} removed={len(payload.get('removed', []))}")
        return
    if isinstance(payload, dict) and "export_id" in payload:
        print(
            f"export_id={payload.get('export_id')} status={payload.get('status')} "
            f"format={payload.get('format')} rows={payload.get('rows_count')} path={payload.get('path')}"
        )
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _print_scheduler_payload(payload, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if isinstance(payload, list):
        if not payload:
            print("No scheduler records")
        for item in payload:
            if "schedule_id" in item:
                print(f"{item.get('id')} {item.get('schedule_id')} {item.get('status')} {item.get('task_id') or ''}".strip())
            else:
                print(f"{item.get('id')} {item.get('spider_id')} {item.get('status')} next={item.get('next_run_at')}")
        return
    if isinstance(payload, dict) and "schedule" in payload:
        schedule = payload.get("schedule")
        if schedule:
            print(f"Registered schedule {schedule['id']} next={schedule.get('next_run_at')}")
        else:
            print(f"No automatic schedule registered for {payload.get('spider_id')}")
        return
    if isinstance(payload, dict) and "id" in payload:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _handle_incremental_command(args: argparse.Namespace, store: FileStore) -> int:
    if args.incremental_command == "watermark" and args.watermark_command == "list":
        print(json.dumps(store.list_watermarks(spider_id=args.spider_id), ensure_ascii=False, indent=2))
        return 0

    if args.incremental_command == "checkpoint":
        if args.checkpoint_command == "list":
            print(json.dumps(store.list_checkpoints(spider_id=args.spider_id), ensure_ascii=False, indent=2))
            return 0
        if args.checkpoint_command == "resume":
            task = CrawlerEngine(store=store).resume_task(args.task_id)
            print(json.dumps({"task_id": task.id, "status": task.status.value, **task.to_dict()}, ensure_ascii=False, indent=2))
            return 0 if task.status.value == "success" else 1

    return 2


def _handle_storage_command(args: argparse.Namespace, store: FileStore) -> int:
    if args.storage_command == "check":
        result = store.check_storage()
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Storage OK" if result["ok"] else "Storage has errors")
            for warning in result["warnings"]:
                print(f"WARNING {warning['path']}: {warning['message']}")
            for error in result["errors"]:
                print(f"ERROR {error['path']}: {error['message']}")
        return 0 if result["ok"] else 1

    if args.storage_command == "repair":
        result = store.repair_storage(dry_run=args.dry_run)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            mode = "dry-run" if result["dry_run"] else "apply"
            print(f"Storage repair {mode}: {len(result['actions'])} action(s)")
            for action in result["actions"]:
                target = f" -> {action['target']}" if action.get("target") else ""
                print(f"{action['action']} {action['path']}{target}")
        return 0

    if args.storage_command == "snapshot":
        if args.snapshot_command == "create":
            manifest = store.create_snapshot(name=args.name, include_results=args.include_results)
            if args.json:
                print(json.dumps(manifest, ensure_ascii=False, indent=2))
            else:
                print(f"Created snapshot {manifest['snapshot_id']} ({manifest['file_count']} files)")
            return 0

        if args.snapshot_command == "list":
            snapshots = store.list_snapshots()
            if args.json:
                print(json.dumps(snapshots, ensure_ascii=False, indent=2))
            else:
                if not snapshots:
                    print("No snapshots")
                for snapshot in snapshots:
                    print(f"{snapshot.get('snapshot_id')} {snapshot.get('created_at', '')}".strip())
            return 0

        if args.snapshot_command == "restore":
            result = store.restore_snapshot(args.snapshot_id, dry_run=args.dry_run)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                mode = "dry-run" if result["dry_run"] else "apply"
                print(f"Snapshot restore {mode}: {result['snapshot_id']}")
                for action in result["actions"]:
                    print(f"{action['source']} -> {action['target']}")
            return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())

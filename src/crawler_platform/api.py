from __future__ import annotations

import json
from pathlib import Path

from .api_errors import ApiError, ExportApiError, InvalidStateApiError, NotFoundApiError, StorageApiError, ValidationApiError
from .api_models import OPENAPI_COMPONENT_SCHEMAS, OPENAPI_TAGS
from .api_utils import api_error_response, api_success, apply_collection_query, is_api_envelope
from .config_loader import load_spider_config
from .engine import CrawlerEngine
from .examples import get_example, list_examples, smoke_examples, validate_examples
from .exporter import ExportError, ExportService
from .lifecycle import InvalidLifecycleTransitionError, TaskLifecycleService, WorkerLifecycleService
from .models import SpiderConfig
from .scheduler import SchedulerError, SchedulerService
from .storage import FileStore, StorageError
from .validation import SpiderConfigValidationError, ensure_valid_spider_config, validate_spider_config
from .worker import WorkerService


def create_app(data_dir: str | Path = "data", fetcher=None, playwright_fetcher=None):
    try:
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.exceptions import RequestValidationError
        from fastapi.openapi.utils import get_openapi
        from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
        from fastapi.staticfiles import StaticFiles
    except Exception as exc:
        raise RuntimeError("FastAPI API requires installing the api extra.") from exc

    store = FileStore(data_dir)
    engine = CrawlerEngine(store=store, fetcher=fetcher, playwright_fetcher=playwright_fetcher)
    scheduler = SchedulerService(store=store, engine=engine)
    worker = WorkerService(store=store, engine=engine, fetcher=fetcher, playwright_fetcher=playwright_fetcher)
    exporter = ExportService(store)
    task_lifecycle = TaskLifecycleService(store=store, operator="api")
    worker_lifecycle = WorkerLifecycleService(store=store, operator="api")
    app = FastAPI(title="Crawler Platform", version="0.1.0", openapi_tags=OPENAPI_TAGS)
    admin_dir = Path(__file__).parent / "web" / "admin"
    admin_assets_dir = admin_dir / "assets"
    if admin_assets_dir.exists():
        app.mount("/admin/assets", StaticFiles(directory=admin_assets_dir), name="admin-assets")

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status_code,
            content=api_error_response(exc.code, exc.message, details=exc.details, request_id=_request_id(request), trace_id=_trace_id(request)),
        )

    @app.exception_handler(SpiderConfigValidationError)
    async def handle_spider_validation_error(request: Request, exc: SpiderConfigValidationError):
        return JSONResponse(
            status_code=422,
            content=api_error_response(
                "VALIDATION_ERROR",
                "Spider config validation failed",
                details=exc.result.to_dict().get("errors", []),
                request_id=_request_id(request),
                trace_id=_trace_id(request),
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=api_error_response(
                "VALIDATION_ERROR",
                "Request validation failed",
                details=[{"loc": list(error.get("loc", [])), "message": error.get("msg", "")} for error in exc.errors()],
                request_id=_request_id(request),
                trace_id=_trace_id(request),
            ),
        )

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, exc: HTTPException):
        code = _http_error_code(exc.status_code, str(exc.detail))
        return JSONResponse(
            status_code=exc.status_code,
            content=api_error_response(code, str(exc.detail), request_id=_request_id(request), trace_id=_trace_id(request)),
        )

    @app.exception_handler(FileNotFoundError)
    async def handle_file_not_found(request: Request, exc: FileNotFoundError):
        return JSONResponse(
            status_code=404,
            content=api_error_response("NOT_FOUND", str(exc) or "Resource not found", request_id=_request_id(request), trace_id=_trace_id(request)),
        )

    @app.exception_handler(StorageError)
    async def handle_storage_error(request: Request, exc: StorageError):
        return JSONResponse(
            status_code=500,
            content=api_error_response("STORAGE_ERROR", str(exc), request_id=_request_id(request), trace_id=_trace_id(request)),
        )

    @app.exception_handler(InvalidLifecycleTransitionError)
    async def handle_invalid_lifecycle(request: Request, exc: InvalidLifecycleTransitionError):
        return JSONResponse(
            status_code=409,
            content=api_error_response("INVALID_STATE", str(exc), request_id=_request_id(request), trace_id=_trace_id(request)),
        )

    @app.exception_handler(Exception)
    async def handle_internal_error(request: Request, exc: Exception):
        message = str(exc) or type(exc).__name__
        return JSONResponse(
            status_code=500,
            content=api_error_response("INTERNAL_ERROR", message, request_id=_request_id(request), trace_id=_trace_id(request)),
        )

    @app.middleware("http")
    async def api_envelope_middleware(request: Request, call_next):
        response = await call_next(request)
        return await _wrap_json_response(request, response, JSONResponse)

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes, tags=OPENAPI_TAGS)
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        components.update(OPENAPI_COMPONENT_SCHEMAS)
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                if isinstance(operation, dict):
                    for status in ("200", "201", "400", "404", "409", "422", "500"):
                        response = operation.setdefault("responses", {}).setdefault(status, {"description": "API response"})
                        response.setdefault("content", {}).setdefault("application/json", {}).setdefault("schema", {"$ref": "#/components/schemas/ApiResponse"})
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi

    @app.get("/health")
    def health():
        return {"status": "ok", "storage": str(store.root), "database": {"enabled": False}}

    @app.get("/runtime/info")
    def runtime_info():
        metadata = store.read_storage_metadata()
        return {
            "name": "Crawler Platform",
            "version": app.version,
            "storage_version": metadata.get("storage_version"),
            "storage_root": str(store.root),
            "database": {"enabled": False, "runtime_dependency": False},
        }

    @app.get("/runtime/capabilities")
    def runtime_capabilities():
        metadata = store.read_storage_metadata()
        return {
            "features": metadata.get("features", {}),
            "api": {
                "response_envelope": True,
                "error_envelope": True,
                "pagination_filtering_sorting": True,
                "openapi": True,
            },
            "storage": {"type": "FileStore", "root": str(store.root)},
            "database": {"enabled": False},
        }

    @app.get("/runtime/storage")
    def runtime_storage():
        return {"root": str(store.root), "health": store.check_storage(), "metadata": store.read_storage_metadata()}

    @app.get("/", response_class=HTMLResponse)
    def console():
        return HTMLResponse(_console_html())

    @app.get("/admin", include_in_schema=False)
    @app.get("/admin/", include_in_schema=False)
    def web_admin():
        return FileResponse(admin_dir / "index.html")

    @app.get("/examples")
    def api_list_examples(include_templates: bool = True):
        return list_examples(include_templates=include_templates)

    @app.get("/examples/{example_id}")
    def api_get_example(example_id: str):
        try:
            return get_example(example_id)
        except FileNotFoundError as exc:
            raise NotFoundApiError("Example not found") from exc

    @app.post("/examples/validate")
    def api_validate_examples():
        return validate_examples()

    @app.post("/examples/smoke")
    def api_smoke_examples(payload: dict | None = None):
        data = payload or {}
        smoke_data_dir = data.get("data_dir") or (store.root / "examples_smoke")
        ids = data.get("ids")
        if ids is not None and not isinstance(ids, list):
            raise ValidationApiError("ids must be a list of example ids")
        return smoke_examples(smoke_data_dir, ids=ids)

    @app.get("/spiders")
    def list_spiders(enabled: bool | None = None):
        return store.list_spider_configs(enabled=enabled)

    @app.post("/spiders")
    def save_spider(payload: dict):
        spider = ensure_valid_spider_config(payload)
        store.save_spider(spider)
        return spider.to_dict()

    @app.get("/spiders/{spider_id}")
    def get_spider(spider_id: str):
        try:
            return store.load_spider(spider_id).to_dict()
        except FileNotFoundError as exc:
            raise NotFoundApiError("Spider not found") from exc

    @app.put("/spiders/{spider_id}")
    def update_spider(spider_id: str, payload: dict):
        data = dict(payload or {})
        data["id"] = spider_id
        spider = ensure_valid_spider_config(data)
        store.save_spider(spider)
        return spider.to_dict()

    @app.delete("/spiders/{spider_id}")
    def delete_spider(spider_id: str):
        try:
            return store.delete_spider_config(spider_id)
        except FileNotFoundError as exc:
            raise NotFoundApiError("Spider not found") from exc

    @app.post("/validate/spider")
    def validate_spider(payload: dict):
        return validate_spider_config(payload).to_dict()

    @app.post("/spiders/validate")
    def validate_spider_alias(payload: dict):
        return validate_spider_config(payload).to_dict()

    @app.post("/spiders/from-file")
    def save_spider_from_file(path: str):
        spider = load_spider_config(path)
        store.save_spider(spider)
        return spider.to_dict()

    @app.post("/tasks/run/{spider_id}")
    def run_task(spider_id: str, start_url: str | None = None):
        try:
            spider = store.load_spider(spider_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Spider not found") from exc
        persist_spider = True
        if start_url:
            merged = _deep_merge_dicts(spider.to_dict(), {"start_urls": [start_url]})
            merged["id"] = spider_id
            spider = ensure_valid_spider_config(merged)
            persist_spider = False
        task = engine.run(spider, persist_spider=persist_spider)
        return task.to_dict()

    @app.post("/tasks/run")
    def run_task_payload(payload: dict):
        spider_id = payload.get("spider_id")
        persist_spider = True
        if spider_id:
            try:
                spider = store.load_spider(spider_id)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="Spider not found") from exc
            overrides = payload.get("spider")
            if overrides:
                merged = _deep_merge_dicts(spider.to_dict(), overrides)
                merged["id"] = spider_id
                spider = ensure_valid_spider_config(merged)
                persist_spider = False
        else:
            spider = ensure_valid_spider_config(payload.get("spider", payload))
            store.save_spider(spider)
        task = engine.run(spider, task_id=payload.get("task_id"), persist_spider=persist_spider)
        return {"task_id": task.id, "status": task.status.value, **task.to_dict()}

    @app.get("/tasks")
    def list_tasks():
        return store.list_tasks()

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str):
        try:
            task = store.load_task(task_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc
        return task.to_dict()

    @app.post("/tasks/{task_id}/cancel")
    def cancel_task(task_id: str):
        try:
            return task_lifecycle.cancel_task(task_id).to_dict()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc
        except InvalidLifecycleTransitionError as exc:
            raise InvalidStateApiError(str(exc)) from exc

    @app.post("/tasks/{task_id}/pause")
    def pause_task(task_id: str, payload: dict | None = None):
        try:
            reason = payload.get("reason") if isinstance(payload, dict) else None
            return task_lifecycle.pause_task(task_id, reason=reason).to_dict()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc
        except InvalidLifecycleTransitionError as exc:
            raise InvalidStateApiError(str(exc)) from exc

    @app.post("/tasks/{task_id}/resume")
    def resume_task(task_id: str, payload: dict | None = None):
        try:
            reason = payload.get("reason") if isinstance(payload, dict) else None
            return task_lifecycle.resume_task(task_id, reason=reason).to_dict()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc
        except InvalidLifecycleTransitionError as exc:
            raise InvalidStateApiError(str(exc)) from exc

    @app.post("/tasks/{task_id}/retry")
    def retry_task(task_id: str, payload: dict | None = None):
        try:
            reason = payload.get("reason") if isinstance(payload, dict) else None
            return task_lifecycle.retry_task(task_id, reason=reason).to_dict()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc
        except InvalidLifecycleTransitionError as exc:
            raise InvalidStateApiError(str(exc)) from exc

    @app.post("/tasks/{task_id}/rerun")
    def rerun_task(task_id: str, payload: dict | None = None):
        try:
            reason = payload.get("reason") if isinstance(payload, dict) else None
            return task_lifecycle.rerun_task(task_id, reason=reason).to_dict()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc
        except InvalidLifecycleTransitionError as exc:
            raise InvalidStateApiError(str(exc)) from exc

    @app.get("/tasks/{task_id}/events")
    def get_task_events(task_id: str):
        return task_lifecycle.list_task_events(task_id)

    @app.get("/tasks/{task_id}/lifecycle")
    def get_task_lifecycle(task_id: str):
        try:
            return task_lifecycle.get_task_lifecycle(task_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc

    @app.get("/tasks/{task_id}/results")
    def get_results(task_id: str):
        return store.read_records(task_id)

    @app.get("/tasks/{task_id}/report")
    def get_task_report(task_id: str):
        try:
            return store.get_run_report("task", task_id)
        except FileNotFoundError:
            try:
                task = store.load_task(task_id)
            except FileNotFoundError as exc:
                raise NotFoundApiError("Task not found") from exc
            task_data = task.to_dict()
            return {
                "target_type": "task",
                "target_id": task_id,
                "task_id": task_id,
                "spider_id": task.spider_id,
                "status": task_data.get("status"),
                "created_at": task_data.get("created_at") or task_data.get("started_at"),
                "finished_at": task_data.get("finished_at") or task_data.get("completed_at"),
                "total_requests": task_data.get("total_requests", 0),
                "success_requests": task_data.get("success_requests", 0),
                "failed_requests": task_data.get("failed_requests", 0),
                "saved_records": task_data.get("saved_records", task_data.get("saved_count", 0)),
                "record_quality_status": "unknown",
                "warnings_count": len(task_data.get("warnings", [])),
                "errors_count": 1 if task_data.get("error_message") else 0,
                "warnings": task_data.get("warnings", []),
                "warning_summary": task_data.get("warnings", []),
                "error_summary": [task_data.get("error_message")] if task_data.get("error_message") else [],
                "record_samples": store.read_records(task_id, strict=False)[:5],
                "task": task_data,
                "records_count": len(store.read_records(task_id, strict=False)),
            }

    @app.get("/tasks/{task_id}/logs")
    def get_task_logs(task_id: str, level: str | None = None, limit: int | None = None, offset: int = 0):
        try:
            store.load_task(task_id)
        except FileNotFoundError as exc:
            raise NotFoundApiError("Task not found") from exc
        return store.iter_logs(scope="tasks", target_id=task_id, level=level, limit=limit, offset=offset)

    @app.get("/tasks/{task_id}/metrics")
    def get_task_metrics(task_id: str):
        try:
            store.load_task(task_id)
        except FileNotFoundError as exc:
            raise NotFoundApiError("Task not found") from exc
        return store.summarize_metrics(scope="tasks", target_id=task_id)

    @app.get("/tasks/{task_id}/export")
    def export_task(task_id: str, fmt: str = "jsonl"):
        try:
            return exporter.export_task(task_id, fmt=fmt)
        except (ExportError, FileNotFoundError) as exc:
            raise ExportApiError(str(exc)) from exc

    @app.get("/storage/health")
    def storage_health():
        return store.check_storage()

    @app.post("/storage/repair")
    def storage_repair(dry_run: bool = True):
        return store.repair_storage(dry_run=dry_run)

    @app.post("/storage/snapshots")
    def create_storage_snapshot(name: str | None = None, include_results: bool = False):
        return store.create_snapshot(name=name, include_results=include_results)

    @app.get("/storage/snapshots")
    def list_storage_snapshots():
        return store.list_snapshots()

    @app.post("/storage/snapshots/{snapshot_id}/restore")
    def restore_storage_snapshot(snapshot_id: str, dry_run: bool = True):
        return store.restore_snapshot(snapshot_id, dry_run=dry_run)

    @app.get("/sessions")
    def list_sessions():
        return store.list_session_profiles()

    @app.get("/sessions/events")
    def list_session_events(profile_id: str | None = None):
        return store.list_session_events(profile_id=profile_id)

    @app.get("/observability/logs")
    def list_observability_logs(
        task_id: str | None = None,
        job_id: str | None = None,
        schedule_id: str | None = None,
        scheduler_run_id: str | None = None,
        level: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ):
        scope, target_id = _observability_target(task_id=task_id, job_id=job_id, schedule_id=schedule_id, scheduler_run_id=scheduler_run_id)
        return store.iter_logs(scope=scope, target_id=target_id, level=level, limit=limit, offset=offset)

    @app.get("/observability/metrics")
    def get_observability_metrics(
        task_id: str | None = None,
        job_id: str | None = None,
        schedule_id: str | None = None,
        scheduler_run_id: str | None = None,
    ):
        scope, target_id = _observability_target(task_id=task_id, job_id=job_id, schedule_id=schedule_id, scheduler_run_id=scheduler_run_id)
        return store.summarize_metrics(scope=scope, target_id=target_id)

    @app.get("/observability/reports/tasks/{task_id}")
    def get_observability_task_report(task_id: str):
        try:
            return store.get_run_report("task", task_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Task report not found") from exc

    @app.get("/observability/reports/jobs/{job_id}")
    def get_observability_job_report(job_id: str):
        try:
            return store.get_run_report("job", job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job report not found") from exc

    @app.get("/observability/reports/scheduler/{scheduler_run_id}")
    def get_observability_scheduler_report(scheduler_run_id: str):
        try:
            return store.get_run_report("scheduler", scheduler_run_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Scheduler report not found") from exc

    @app.get("/observability/traces/{trace_id}")
    def get_observability_trace(trace_id: str):
        try:
            return store.get_trace(trace_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Trace not found") from exc

    @app.post("/exports/tasks/{task_id}")
    def create_task_export(task_id: str, payload: dict | None = None):
        try:
            return exporter.export_task(task_id, **_api_export_kwargs(payload))
        except (ExportError, FileNotFoundError) as exc:
            raise ExportApiError(str(exc)) from exc

    @app.post("/exports/jobs/{job_id}")
    def create_job_export(job_id: str, payload: dict | None = None):
        try:
            return exporter.export_job(job_id, **_api_export_kwargs(payload))
        except (ExportError, FileNotFoundError) as exc:
            raise ExportApiError(str(exc)) from exc

    @app.post("/exports/scheduler/{scheduler_run_id}")
    def create_scheduler_export(scheduler_run_id: str, payload: dict | None = None):
        try:
            return exporter.export_scheduler_run(scheduler_run_id, **_api_export_kwargs(payload))
        except (ExportError, FileNotFoundError) as exc:
            raise ExportApiError(str(exc)) from exc

    @app.post("/exports/observability/logs")
    def create_observability_logs_export(payload: dict | None = None):
        payload = payload or {}
        try:
            return exporter.export_observability_logs(
                task_id=payload.get("task_id"),
                job_id=payload.get("job_id"),
                schedule_id=payload.get("schedule_id"),
                scheduler_run_id=payload.get("scheduler_run_id"),
                level=payload.get("level"),
                **_api_export_kwargs(payload),
            )
        except ExportError as exc:
            raise ExportApiError(str(exc)) from exc

    @app.get("/exports")
    def list_exports():
        return exporter.list_exports()

    @app.get("/exports/{export_id}")
    def get_export(export_id: str):
        try:
            return exporter.get_export(export_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Export not found") from exc

    @app.get("/exports/{export_id}/download")
    def download_export(export_id: str):
        try:
            manifest = exporter.get_export(export_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Export not found") from exc
        path = Path(str(manifest.get("path") or ""))
        if not path.exists():
            raise HTTPException(status_code=404, detail="Export file not found")
        return FileResponse(path, filename=path.name)

    @app.delete("/exports/{export_id}")
    def delete_export(export_id: str):
        try:
            return exporter.delete_export(export_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Export not found") from exc

    @app.get("/sessions/{profile_id}")
    def get_session(profile_id: str):
        try:
            profile = store.get_session_profile(profile_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session profile not found") from exc
        cookies = {}
        storage_state = None
        try:
            cookies = store.load_cookies(profile_id)
        except FileNotFoundError:
            pass
        try:
            storage_state = store.load_storage_state(profile_id)
        except FileNotFoundError:
            pass
        return {"profile": profile, "cookies": cookies, "storage_state": storage_state}

    @app.delete("/sessions/{profile_id}")
    def delete_session(profile_id: str):
        return store.delete_session_profile(profile_id)

    @app.post("/sessions/{profile_id}/clear")
    def clear_session(profile_id: str):
        return store.delete_session_profile(profile_id)

    @app.get("/incremental/watermarks")
    def list_incremental_watermarks(spider_id: str | None = None):
        return store.list_watermarks(spider_id=spider_id)

    @app.get("/incremental/checkpoints")
    def list_incremental_checkpoints(spider_id: str | None = None):
        return store.list_checkpoints(spider_id=spider_id)

    @app.post("/incremental/checkpoints/{task_id}/resume")
    def resume_incremental_checkpoint(task_id: str):
        try:
            return engine.resume_task(task_id).to_dict()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Task or checkpoint not found") from exc
        except RuntimeError as exc:
            raise InvalidStateApiError(str(exc)) from exc

    @app.get("/scheduler/schedules")
    def list_scheduler_schedules(enabled: bool | None = None, spider_id: str | None = None):
        return scheduler.list_schedules(enabled=enabled, spider_id=spider_id)

    @app.post("/scheduler/schedules")
    def register_scheduler_schedule(payload: dict):
        try:
            spider = ensure_valid_spider_config(payload.get("spider", payload))
            job = scheduler.register_spider_schedule(spider)
            return {"registered": job is not None, "schedule": job.to_dict() if job else None, "spider_id": spider.id}
        except SpiderConfigValidationError:
            raise
        except SchedulerError as exc:
            raise ValidationApiError(str(exc)) from exc
        except StorageError as exc:
            raise StorageApiError(str(exc)) from exc
        except Exception as exc:
            raise ValidationApiError(str(exc)) from exc

    @app.get("/scheduler/schedules/{schedule_id}")
    def get_scheduler_schedule(schedule_id: str):
        try:
            return scheduler.get_schedule(schedule_id).to_dict()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Schedule not found") from exc

    @app.post("/scheduler/schedules/{schedule_id}/trigger")
    def trigger_scheduler_schedule(schedule_id: str):
        try:
            return scheduler.trigger_schedule_now(schedule_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Schedule not found") from exc
        except SchedulerError as exc:
            raise InvalidStateApiError(str(exc)) from exc

    @app.post("/scheduler/schedules/{schedule_id}/pause")
    def pause_scheduler_schedule(schedule_id: str):
        try:
            return scheduler.pause_schedule(schedule_id).to_dict()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Schedule not found") from exc

    @app.post("/scheduler/schedules/{schedule_id}/resume")
    def resume_scheduler_schedule(schedule_id: str):
        try:
            return scheduler.resume_schedule(schedule_id).to_dict()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Schedule not found") from exc

    @app.post("/scheduler/schedules/{schedule_id}/disable")
    def disable_scheduler_schedule(schedule_id: str):
        try:
            return scheduler.disable_schedule(schedule_id).to_dict()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Schedule not found") from exc

    @app.post("/scheduler/run-due")
    def run_due_scheduler_jobs(payload: dict | None = None, now: str | None = None):
        selected_now = payload.get("now") if isinstance(payload, dict) and payload.get("now") else now
        enqueue = bool(payload.get("enqueue")) if isinstance(payload, dict) else False
        return scheduler.run_due_jobs(now=selected_now, enqueue=enqueue)

    @app.post("/scheduler/enqueue-due")
    def enqueue_due_scheduler_jobs(payload: dict | None = None, now: str | None = None):
        selected_now = payload.get("now") if isinstance(payload, dict) and payload.get("now") else now
        return scheduler.enqueue_due_jobs(now=selected_now)

    @app.get("/scheduler/runs")
    def list_scheduler_runs(schedule_id: str | None = None, spider_id: str | None = None):
        return scheduler.list_scheduler_runs(schedule_id=schedule_id, spider_id=spider_id)

    @app.post("/worker/jobs")
    def enqueue_worker_job(payload: dict):
        try:
            data = payload or {}
            if data.get("spider_id") and not data.get("spider"):
                spider = store.load_spider(data["spider_id"])
            else:
                spider = ensure_valid_spider_config(data.get("spider", data))
            job = worker.enqueue_spider_run(
                spider,
                source=data.get("source", "api"),
                priority=int(data.get("priority", 0) or 0),
                run_after=data.get("run_after"),
                schedule_id=data.get("schedule_id"),
                task_id=data.get("task_id"),
                max_attempts=int(data.get("max_attempts", 1) or 1),
                metadata=dict(data.get("metadata", {})),
            )
            return job.to_dict()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Spider not found") from exc
        except SpiderConfigValidationError:
            raise
        except StorageError as exc:
            raise StorageApiError(str(exc)) from exc
        except Exception as exc:
            raise ValidationApiError(str(exc)) from exc

    @app.get("/worker/jobs")
    def list_worker_jobs(status: str | None = None, source: str | None = None, spider_id: str | None = None):
        return store.list_jobs(status=status, source=source, spider_id=spider_id)

    @app.get("/worker/jobs/{job_id}")
    def get_worker_job(job_id: str):
        try:
            return store.get_job(job_id).to_dict()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Worker job not found") from exc

    @app.post("/worker/run-once")
    def run_worker_once(payload: dict | None = None):
        worker_id = payload.get("worker_id") if isinstance(payload, dict) else None
        return worker.run_once(worker_id=worker_id).to_dict()

    @app.post("/worker/run-until-empty")
    def run_worker_until_empty(payload: dict | None = None):
        data = payload or {}
        worker_id = data.get("worker_id") if isinstance(data, dict) else None
        max_jobs = data.get("max_jobs") if isinstance(data, dict) else None
        return worker.run_until_empty(worker_id=worker_id, max_jobs=max_jobs)

    @app.post("/worker/jobs/{job_id}/cancel")
    def cancel_worker_job(job_id: str):
        try:
            return worker.cancel_job(job_id).to_dict()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Worker job not found") from exc
        except InvalidLifecycleTransitionError as exc:
            raise InvalidStateApiError(str(exc)) from exc

    @app.post("/worker/jobs/{job_id}/pause")
    def pause_worker_job(job_id: str, payload: dict | None = None):
        try:
            reason = payload.get("reason") if isinstance(payload, dict) else None
            return worker.pause_job(job_id, reason=reason).to_dict()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Worker job not found") from exc
        except InvalidLifecycleTransitionError as exc:
            raise InvalidStateApiError(str(exc)) from exc

    @app.post("/worker/jobs/{job_id}/resume")
    def resume_worker_job(job_id: str, payload: dict | None = None):
        try:
            reason = payload.get("reason") if isinstance(payload, dict) else None
            return worker.resume_job(job_id, reason=reason).to_dict()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Worker job not found") from exc
        except InvalidLifecycleTransitionError as exc:
            raise InvalidStateApiError(str(exc)) from exc

    @app.post("/worker/jobs/{job_id}/retry")
    def retry_worker_job(job_id: str, payload: dict | None = None):
        try:
            reason = payload.get("reason") if isinstance(payload, dict) else None
            return worker.retry_job(job_id, reason=reason).to_dict()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Worker job not found") from exc
        except InvalidLifecycleTransitionError as exc:
            raise InvalidStateApiError(str(exc)) from exc

    @app.post("/worker/jobs/{job_id}/rerun")
    def rerun_worker_job(job_id: str, payload: dict | None = None):
        try:
            reason = payload.get("reason") if isinstance(payload, dict) else None
            return worker.rerun_job(job_id, reason=reason).to_dict()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Worker job not found") from exc
        except InvalidLifecycleTransitionError as exc:
            raise InvalidStateApiError(str(exc)) from exc

    @app.get("/worker/jobs/{job_id}/events")
    def get_worker_job_events(job_id: str):
        return worker.list_job_events(job_id)

    @app.get("/worker/jobs/{job_id}/lifecycle")
    def get_worker_job_lifecycle(job_id: str):
        try:
            return worker_lifecycle.get_job_lifecycle(job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Worker job not found") from exc

    @app.post("/worker/recover")
    def recover_worker_jobs(payload: dict | None = None, now: str | None = None):
        selected_now = payload.get("now") if isinstance(payload, dict) and payload.get("now") else now
        return worker.recover_expired_jobs(now=selected_now)

    @app.get("/worker/stats")
    def get_worker_stats():
        return worker.stats()

    @app.get("/worker/dead-letters")
    def list_worker_dead_letters():
        return store.list_jobs(status="dead_letter")

    _assign_route_tags(app)
    return app


async def _wrap_json_response(request, response, json_response_cls):
    path = request.url.path
    if _skip_envelope_path(path):
        return response
    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type.lower():
        return response
    body = b""
    async for chunk in response.body_iterator:
        body += chunk
    if not body:
        return response
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return json_response_cls(status_code=response.status_code, content=api_success(body.decode("utf-8", errors="replace"), request_id=_request_id(request), trace_id=_trace_id(request)))
    if is_api_envelope(payload):
        content = payload
    elif response.status_code >= 400:
        message = payload.get("detail") if isinstance(payload, dict) else str(payload)
        content = api_error_response(_http_error_code(response.status_code, str(message)), str(message), request_id=_request_id(request), trace_id=_trace_id(request))
    else:
        data, page_meta = apply_collection_query(payload, request.query_params)
        content = api_success(data, request_id=_request_id(request), trace_id=_trace_id(request), meta=page_meta)
    headers = {key: value for key, value in response.headers.items() if key.lower() not in {"content-length", "content-type"}}
    return json_response_cls(status_code=response.status_code, content=content, headers=headers)


def _skip_envelope_path(path: str) -> bool:
    if path in {"/", "/docs", "/redoc", "/openapi.json"} or path.startswith("/admin"):
        return True
    return path.endswith("/download")


def _request_id(request) -> str | None:
    return request.headers.get("X-Request-ID") or request.headers.get("x-request-id")


def _trace_id(request) -> str | None:
    return request.headers.get("X-Trace-ID") or request.headers.get("x-trace-id")


def _deep_merge_dicts(base: dict, overrides: dict) -> dict:
    merged = dict(base)
    for key, value in overrides.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dicts(current, value)
        else:
            merged[key] = value
    return merged


def _http_error_code(status_code: int, message: str) -> str:
    if status_code == 404:
        return "NOT_FOUND"
    if status_code == 409:
        return "CONFLICT"
    if status_code == 422:
        return "VALIDATION_ERROR"
    if "invalid" in message.lower() or "cannot" in message.lower():
        return "INVALID_STATE"
    if status_code >= 500:
        return "INTERNAL_ERROR"
    return "VALIDATION_ERROR"


def _assign_route_tags(app) -> None:
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path:
            continue
        if path in {"/health"} or path.startswith("/runtime"):
            route.tags = ["runtime"]
        elif path.startswith("/spiders") or path.startswith("/validate"):
            route.tags = ["spiders"]
        elif path.startswith("/examples"):
            route.tags = ["examples"]
        elif _is_lifecycle_path(path):
            route.tags = ["lifecycle"]
        elif path.startswith("/tasks"):
            route.tags = ["tasks"]
        elif path.startswith("/storage") or path.startswith("/incremental"):
            route.tags = ["storage"]
        elif path.startswith("/scheduler"):
            route.tags = ["scheduler"]
        elif path.startswith("/worker"):
            route.tags = ["worker"]
        elif path.startswith("/sessions"):
            route.tags = ["sessions"]
        elif path.startswith("/observability"):
            route.tags = ["observability"]
        elif path.startswith("/exports"):
            route.tags = ["exports"]


def _is_lifecycle_path(path: str) -> bool:
    lifecycle_actions = ("/pause", "/resume", "/cancel", "/retry", "/rerun", "/events", "/lifecycle")
    return any(path.endswith(action) for action in lifecycle_actions) and (path.startswith("/tasks") or path.startswith("/worker/jobs"))


def _console_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Crawler Platform</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 0; background: #f7f7f8; color: #222; }
    header { background: #1f2937; color: white; padding: 16px 24px; }
    main { max-width: 1120px; margin: 24px auto; padding: 0 16px; display: grid; gap: 16px; }
    section { background: white; border: 1px solid #ddd; border-radius: 8px; padding: 16px; }
    button { border: 1px solid #1f2937; background: #1f2937; color: white; border-radius: 6px; padding: 8px 12px; }
    pre { background: #111827; color: #e5e7eb; padding: 12px; overflow: auto; border-radius: 6px; }
    input { padding: 8px; min-width: 280px; }
  </style>
</head>
<body>
  <header><h1>Crawler Platform</h1></header>
  <main>
    <section>
      <h2>Tasks</h2>
      <button onclick="loadTasks()">Refresh</button>
      <pre id="tasks">[]</pre>
    </section>
    <section>
      <h2>Run Spider</h2>
      <input id="spiderId" placeholder="spider id">
      <button onclick="runSpider()">Run</button>
      <pre id="runResult">{}</pre>
    </section>
  </main>
  <script>
    async function loadTasks() {
      document.getElementById('tasks').textContent = JSON.stringify(await (await fetch('/tasks')).json(), null, 2);
    }
    async function runSpider() {
      const id = document.getElementById('spiderId').value;
      document.getElementById('runResult').textContent = JSON.stringify(await (await fetch('/tasks/run/' + encodeURIComponent(id), { method: 'POST' })).json(), null, 2);
      loadTasks();
    }
    loadTasks();
  </script>
</body>
</html>"""


def _observability_target(
    *,
    task_id: str | None = None,
    job_id: str | None = None,
    schedule_id: str | None = None,
    scheduler_run_id: str | None = None,
) -> tuple[str | None, str | None]:
    if task_id:
        return "tasks", task_id
    if job_id:
        return "jobs", job_id
    if scheduler_run_id:
        return "scheduler", scheduler_run_id
    if schedule_id:
        return "scheduler", schedule_id
    return None, None


def _api_export_kwargs(payload: dict | None) -> dict[str, object]:
    payload = payload or {}
    options: dict[str, object] = {}
    if payload.get("format"):
        options["fmt"] = payload["format"]
    if payload.get("output"):
        options["output"] = payload["output"]
    config = payload.get("config") or payload.get("export")
    if isinstance(config, dict):
        options["config"] = config
    for source, target in [
        ("include_fields", "include_fields"),
        ("exclude_fields", "exclude_fields"),
        ("flatten", "flatten"),
        ("include_metadata", "include_metadata"),
    ]:
        if source in payload:
            options[target] = payload[source]
    return options

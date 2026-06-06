from __future__ import annotations

import csv
import json
import uuid
import zipfile
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from .models import ExportConfig

SUPPORTED_EXPORT_FORMATS = {"json", "jsonl", "csv", "xlsx"}
_SENSITIVE_TOKENS = ("password", "secret", "token", "authorization", "cookie")
_SENSITIVE_EXACT = {"session_id", "sessionid", "sid"}


class ExportError(RuntimeError):
    pass


def export_records(
    records: list[dict[str, Any]],
    output: str | Path,
    fmt: str | None = None,
    *,
    config: ExportConfig | dict[str, Any] | None = None,
    include_fields: list[str] | None = None,
    exclude_fields: list[str] | None = None,
    flatten: str | None = None,
    include_metadata: bool | None = None,
) -> Path:
    path = Path(output)
    selected_format = (fmt or path.suffix.lstrip(".") or "jsonl").lower()
    options = _export_options(
        config,
        selected_format=selected_format,
        include_fields=include_fields,
        exclude_fields=exclude_fields,
        flatten=flatten,
        include_metadata=include_metadata,
    )
    rows = prepare_records(records, options)
    _write_rows(rows, path, selected_format, options)
    return path


def prepare_records(records: list[dict[str, Any]], config: ExportConfig | dict[str, Any] | None = None) -> list[dict[str, Any]]:
    options = _export_options(config)
    return [_prepare_record(record, options) for record in records]


class ExportService:
    def __init__(self, store) -> None:
        self.store = store

    def export_task(
        self,
        task_id: str,
        *,
        fmt: str | None = None,
        output: str | Path | None = None,
        config: ExportConfig | dict[str, Any] | None = None,
        limit: int | None = None,
        offset: int = 0,
        **overrides: Any,
    ) -> dict[str, Any]:
        try:
            task = self.store.load_task(task_id).to_dict()
        except FileNotFoundError:
            task = None
        records = list(self.store.iter_records(task_id, limit=limit, offset=offset))
        options = self._options(config, fmt=fmt, **overrides)
        if options.include_metadata and task is not None:
            records = [{**record, "_metadata": {"task": task}} for record in records]
        return self._create_export(
            records,
            source_type="task",
            source_id=task_id,
            fmt=fmt,
            output=output,
            config=options,
            metadata={"task": task, "window": {"limit": limit, "offset": max(0, int(offset or 0))}},
        )

    def export_task_report(self, task_id: str, *, fmt: str | None = None, output: str | Path | None = None, config: ExportConfig | dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
        report = self.store.get_run_report("task", task_id)
        return self._create_export([report], source_type="task_report", source_id=task_id, fmt=fmt, output=output, config=self._options(config, fmt=fmt, **overrides))

    def export_job(self, job_id: str, *, fmt: str | None = None, output: str | Path | None = None, config: ExportConfig | dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
        job = self.store.get_job(job_id).to_dict()
        records = self.store.read_records(job["task_id"]) if job.get("task_id") else []
        report = _maybe_report(self.store, "job", job_id)
        options = self._options(config, fmt=fmt, **overrides)
        if records:
            if options.include_metadata:
                records = [{**record, "_metadata": {"job": job, "job_report": report}} for record in records]
        else:
            records = [{"job": job, "job_report": report}]
        return self._create_export(records, source_type="job", source_id=job_id, fmt=fmt, output=output, config=options, metadata={"job": job, "job_report": report})

    def export_scheduler_run(self, scheduler_run_id: str, *, fmt: str | None = None, output: str | Path | None = None, config: ExportConfig | dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
        run = _find_scheduler_run(self.store, scheduler_run_id)
        report = _maybe_report(self.store, "scheduler", scheduler_run_id)
        payload = report or run
        if run and report:
            payload = {**report, "scheduler_run": run}
        return self._create_export([payload], source_type="scheduler", source_id=scheduler_run_id, fmt=fmt, output=output, config=self._options(config, fmt=fmt, **overrides), metadata={"scheduler_run": run, "scheduler_report": report})

    def export_observability_logs(
        self,
        *,
        task_id: str | None = None,
        job_id: str | None = None,
        schedule_id: str | None = None,
        scheduler_run_id: str | None = None,
        level: str | None = None,
        fmt: str | None = None,
        output: str | Path | None = None,
        config: ExportConfig | dict[str, Any] | None = None,
        **overrides: Any,
    ) -> dict[str, Any]:
        scope, target_id = _observability_target(task_id=task_id, job_id=job_id, schedule_id=schedule_id, scheduler_run_id=scheduler_run_id)
        records = self.store.iter_logs(scope=scope, target_id=target_id, level=level)
        return self._create_export(records, source_type="observability_logs", source_id=target_id or "all", fmt=fmt, output=output, config=self._options(config, fmt=fmt, **overrides), metadata={"scope": scope, "target_id": target_id, "level": level})

    def export_lifecycle_events(self, target_type: str | None = None, target_id: str | None = None, *, fmt: str | None = None, output: str | Path | None = None, config: ExportConfig | dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
        records = self.store.list_lifecycle_events(target_type=target_type, target_id=target_id)
        return self._create_export(records, source_type="lifecycle_events", source_id=target_id or target_type or "all", fmt=fmt, output=output, config=self._options(config, fmt=fmt, **overrides), metadata={"target_type": target_type, "target_id": target_id})

    def list_exports(self) -> list[dict[str, Any]]:
        return self.store.list_exports()

    def get_export(self, export_id: str) -> dict[str, Any]:
        return self.store.get_export(export_id)

    def delete_export(self, export_id: str) -> dict[str, Any]:
        return self.store.delete_export(export_id)

    def _options(self, config: ExportConfig | dict[str, Any] | None, *, fmt: str | None = None, **overrides: Any) -> ExportConfig:
        return _export_options(config, selected_format=fmt, **overrides)

    def _create_export(
        self,
        records: list[dict[str, Any]],
        *,
        source_type: str,
        source_id: str,
        fmt: str | None,
        output: str | Path | None,
        config: ExportConfig,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        selected_format = (fmt or config.default_format).lower()
        _ensure_format(selected_format, config)
        export_id = uuid.uuid4().hex
        path = _export_output_path(self.store, output, export_id, selected_format, config)
        rows = prepare_records(records, config)
        _write_rows(rows, path, selected_format, config)
        manifest = {
            "export_id": export_id,
            "source_type": source_type,
            "source_id": source_id,
            "format": selected_format,
            "path": str(path),
            "filename": path.name,
            "rows_count": len(rows),
            "columns": _columns(rows, config),
            "status": "success",
            "created_at": _now(),
            "file_size_bytes": path.stat().st_size if path.exists() else 0,
            "options": asdict(config),
            "metadata": metadata or {},
        }
        return self.store.create_export(manifest)


def _export_options(
    config: ExportConfig | dict[str, Any] | None = None,
    *,
    selected_format: str | None = None,
    include_fields: list[str] | None = None,
    exclude_fields: list[str] | None = None,
    flatten: str | None = None,
    include_metadata: bool | None = None,
    **overrides: Any,
) -> ExportConfig:
    options = config if isinstance(config, ExportConfig) else ExportConfig.from_dict(config)
    patch = {key: value for key, value in overrides.items() if value is not None and hasattr(options, key)}
    if selected_format:
        patch["default_format"] = selected_format.lower()
    if include_fields is not None:
        patch["include_fields"] = include_fields
    if exclude_fields is not None:
        patch["exclude_fields"] = exclude_fields
    if flatten is not None:
        patch["nested_strategy"] = flatten
    if include_metadata is not None:
        patch["include_metadata"] = include_metadata
    if patch:
        options = replace(options, **patch)
    options.formats = [str(item).lower() for item in options.formats]
    options.default_format = str(options.default_format).lower()
    _ensure_format(options.default_format, options)
    if options.nested_strategy not in {"flatten_dot", "flatten_underscore", "json_string"}:
        raise ExportError(f"Unsupported nested strategy: {options.nested_strategy}")
    if options.list_strategy not in {"json_string", "join"}:
        raise ExportError(f"Unsupported list strategy: {options.list_strategy}")
    return options


def _ensure_format(fmt: str, options: ExportConfig) -> None:
    if fmt not in SUPPORTED_EXPORT_FORMATS:
        raise ExportError(f"Unsupported export format: {fmt}")
    if options.formats and fmt not in options.formats:
        raise ExportError(f"Export format {fmt} is not enabled")


def _prepare_record(record: dict[str, Any], options: ExportConfig) -> dict[str, Any]:
    shaped: dict[str, Any] = {}
    for key, value in record.items():
        if key.startswith("_") and not options.include_metadata and not (key == "_dedup" and options.include_dedup):
            continue
        if key == "_dedup" and not options.include_dedup:
            continue
        _add_value(shaped, key, value, options)
    filtered = _filter_fields(shaped, options)
    return {options.field_aliases.get(key, key): value for key, value in filtered.items()}


def _add_value(target: dict[str, Any], key: str, value: Any, options: ExportConfig) -> None:
    if options.redact_sensitive and _is_sensitive_key(key):
        target[key] = "***REDACTED***"
        return
    if isinstance(value, dict):
        if options.nested_strategy == "json_string":
            target[key] = json.dumps(_redact_tree(value) if options.redact_sensitive else value, ensure_ascii=False, sort_keys=True)
            return
        separator = "." if options.nested_strategy == "flatten_dot" else "_"
        for child_key, child_value in value.items():
            _add_value(target, f"{key}{separator}{child_key}", child_value, options)
        return
    if isinstance(value, list):
        if options.list_strategy == "join":
            target[key] = options.join_separator.join(_stringify_list_item(item, options) for item in value)
        else:
            payload = _redact_tree(value) if options.redact_sensitive else value
            target[key] = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return
    target[key] = value


def _filter_fields(record: dict[str, Any], options: ExportConfig) -> dict[str, Any]:
    include = list(options.include_fields or [])
    exclude = set(options.exclude_fields or [])
    if include:
        return {key: record.get(key, "") for key in include if key not in exclude}
    return {key: value for key, value in record.items() if key not in exclude}


def _stringify_list_item(value: Any, options: ExportConfig) -> str:
    payload = _redact_tree(value) if options.redact_sensitive else value
    if isinstance(payload, (dict, list)):
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return "" if payload is None else str(payload)


def _redact_tree(value: Any, key_path: str = "") -> Any:
    if isinstance(value, dict):
        return {key: ("***REDACTED***" if _is_sensitive_key(f"{key_path}.{key}" if key_path else key) else _redact_tree(item, f"{key_path}.{key}" if key_path else key)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_tree(item, key_path) for item in value]
    return value


def _is_sensitive_key(key: str) -> bool:
    parts = [part.lower() for part in key.replace("[", ".").replace("]", "").split(".") if part]
    return any(part in _SENSITIVE_EXACT or any(token in part for token in _SENSITIVE_TOKENS) for part in parts)


def _write_rows(records: list[dict[str, Any]], path: Path, fmt: str, options: ExportConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    elif fmt == "jsonl":
        path.write_text("\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in records), encoding="utf-8")
    elif fmt == "csv":
        _write_csv(records, path, options)
    elif fmt == "xlsx":
        _write_xlsx(records, path, options)
    else:
        raise ExportError(f"Unsupported export format: {fmt}")


def _columns(records: list[dict[str, Any]], options: ExportConfig | None = None) -> list[str]:
    columns: list[str] = []
    for record in records:
        for key in record:
            if key not in columns:
                columns.append(key)
    if not columns and options and options.include_fields:
        excluded = set(options.exclude_fields or [])
        columns = [options.field_aliases.get(key, key) for key in options.include_fields if key not in excluded]
    return columns


def _write_csv(records: list[dict[str, Any]], path: Path, options: ExportConfig) -> None:
    columns = _columns(records, options)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def _write_xlsx(records: list[dict[str, Any]], path: Path, options: ExportConfig) -> None:
    columns = _columns(records, options)
    rows = [columns] + [[record.get(column, "") for column in columns] for record in records]
    sheet_rows = []
    for row_idx, row in enumerate(rows, start=1):
        cells = []
        for col_idx, value in enumerate(row, start=1):
            ref = f"{_column_name(col_idx)}{row_idx}"
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape("" if value is None else str(value))}</t></is></c>')
        sheet_rows.append(f'<row r="{row_idx}">{"".join(cells)}</row>')
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types_xml())
        archive.writestr("_rels/.rels", _rels_xml())
        archive.writestr("xl/workbook.xml", _workbook_xml())
        archive.writestr("xl/_rels/workbook.xml.rels", _workbook_rels_xml())
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)


def _export_output_path(store, output: str | Path | None, export_id: str, fmt: str, config: ExportConfig) -> Path:
    if output is None:
        base = Path(config.output_dir) if config.output_dir else store.exports_files_dir
        return base / f"{export_id}.{fmt}"
    path = Path(output)
    if path.suffix:
        return path
    return path / f"{export_id}.{fmt}"


def _find_scheduler_run(store, scheduler_run_id: str) -> dict[str, Any] | None:
    for run in store.list_scheduler_runs():
        if run.get("id") == scheduler_run_id:
            return run
    return None


def _maybe_report(store, target_type: str, target_id: str) -> dict[str, Any] | None:
    try:
        return store.get_run_report(target_type, target_id)
    except FileNotFoundError:
        return None


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


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _content_types_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""


def _rels_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""


def _workbook_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="results" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""


def _workbook_rels_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

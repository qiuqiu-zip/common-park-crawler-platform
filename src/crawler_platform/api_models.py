from __future__ import annotations


OPENAPI_TAGS = [
    {"name": "runtime", "description": "Runtime health, storage and capability metadata."},
    {"name": "examples", "description": "Indexed local examples, templates, validation and smoke checks."},
    {"name": "spiders", "description": "Spider configuration validation and management."},
    {"name": "tasks", "description": "Task execution, task records, results, logs, metrics and reports."},
    {"name": "storage", "description": "FileStore health, repair and snapshots."},
    {"name": "scheduler", "description": "Schedules and scheduler run records."},
    {"name": "worker", "description": "Worker queue, job execution, recovery and stats."},
    {"name": "lifecycle", "description": "Task and worker job lifecycle transitions and events."},
    {"name": "sessions", "description": "Session profiles, cookies, storage state and events."},
    {"name": "observability", "description": "Structured logs, metrics, reports and traces."},
    {"name": "exports", "description": "Result/report/log export creation and manifests."},
]


OPENAPI_COMPONENT_SCHEMAS = {
    "ApiMeta": {
        "type": "object",
        "required": ["request_id", "trace_id", "timestamp"],
        "properties": {
            "request_id": {"type": "string"},
            "trace_id": {"type": "string"},
            "timestamp": {"type": "string", "format": "date-time"},
            "pagination": {"$ref": "#/components/schemas/PaginationMeta"},
        },
        "additionalProperties": True,
    },
    "PaginationMeta": {
        "type": "object",
        "required": ["total", "limit", "offset", "has_more"],
        "properties": {
            "total": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 0},
            "offset": {"type": "integer", "minimum": 0},
            "has_more": {"type": "boolean"},
        },
    },
    "ApiErrorPayload": {
        "type": "object",
        "required": ["code", "message", "details"],
        "properties": {
            "code": {
                "type": "string",
                "enum": [
                    "VALIDATION_ERROR",
                    "NOT_FOUND",
                    "CONFLICT",
                    "INVALID_STATE",
                    "STORAGE_ERROR",
                    "ENGINE_ERROR",
                    "EXPORT_ERROR",
                    "INTERNAL_ERROR",
                ],
            },
            "message": {"type": "string"},
            "details": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        },
    },
    "ApiResponse": {
        "type": "object",
        "required": ["ok", "data", "error", "meta"],
        "properties": {
            "ok": {"type": "boolean"},
            "data": {},
            "error": {"anyOf": [{"$ref": "#/components/schemas/ApiErrorPayload"}, {"type": "null"}]},
            "meta": {"$ref": "#/components/schemas/ApiMeta"},
        },
    },
    "SpiderPayload": {"type": "object", "additionalProperties": True},
    "TaskRunRequest": {
        "type": "object",
        "properties": {
            "spider_id": {"type": "string"},
            "spider": {"$ref": "#/components/schemas/SpiderPayload"},
            "task_id": {"type": "string"},
        },
        "additionalProperties": True,
    },
    "ExportRequest": {
        "type": "object",
        "properties": {
            "format": {"type": "string", "enum": ["json", "jsonl", "csv", "xlsx"]},
            "output": {"type": "string"},
            "include_fields": {"type": "array", "items": {"type": "string"}},
            "exclude_fields": {"type": "array", "items": {"type": "string"}},
            "flatten": {"type": "boolean"},
            "include_metadata": {"type": "boolean"},
        },
        "additionalProperties": True,
    },
}

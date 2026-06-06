-- Future database migration sketch only.
-- The runtime platform does not execute this file and remains file-backed.

CREATE TABLE spiders (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  config_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE tasks (
  id TEXT PRIMARY KEY,
  spider_id TEXT NOT NULL REFERENCES spiders(id),
  status TEXT NOT NULL,
  total_seen INTEGER NOT NULL DEFAULT 0,
  saved_count INTEGER NOT NULL DEFAULT 0,
  skipped_duplicates INTEGER NOT NULL DEFAULT 0,
  failed_count INTEGER NOT NULL DEFAULT 0,
  error_type TEXT,
  error_message TEXT,
  started_at TEXT,
  finished_at TEXT
);

CREATE TABLE records (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  spider_id TEXT NOT NULL REFERENCES spiders(id),
  unique_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (spider_id, unique_hash)
);


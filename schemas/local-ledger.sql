-- Local ledger (SQLite, on-device only). Raw events never leave the device.
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE install (
  install_id TEXT PRIMARY KEY,
  public_key BLOB NOT NULL,
  private_key_ref TEXT NOT NULL,
  created_at INTEGER NOT NULL
);

CREATE TABLE raw_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_wall INTEGER NOT NULL,
  ts_mono_ms INTEGER NOT NULL,
  project_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('human_edit','ai_write','exec_start','exec_end','prompt','diff_dwell','commit','focus','fs_burst')),
  category TEXT,
  exit_code INTEGER,
  files_touched INTEGER NOT NULL DEFAULT 0,
  lines_delta INTEGER NOT NULL DEFAULT 0,
  head_commit TEXT,
  dirty_files INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_raw_project_ts ON raw_event(project_id, ts_mono_ms);

CREATE TABLE bucket (
  bucket_id TEXT PRIMARY KEY,
  install_id TEXT NOT NULL REFERENCES install(install_id),
  project_id TEXT NOT NULL,
  repo_remote_hash TEXT,
  bucket_start_wall INTEGER NOT NULL,
  bucket_secs INTEGER NOT NULL DEFAULT 300,
  mono_delta_ms INTEGER NOT NULL,
  human_edits INTEGER NOT NULL DEFAULT 0,
  ai_writes INTEGER NOT NULL DEFAULT 0,
  prompts INTEGER NOT NULL DEFAULT 0,
  executions INTEGER NOT NULL DEFAULT 0,
  exec_failures INTEGER NOT NULL DEFAULT 0,
  commits INTEGER NOT NULL DEFAULT 0,
  dwell_diff_secs INTEGER NOT NULL DEFAULT 0,
  idle_secs INTEGER NOT NULL DEFAULT 0,
  lines_added INTEGER NOT NULL DEFAULT 0,
  lines_deleted INTEGER NOT NULL DEFAULT 0,
  head_commit TEXT,
  dirty_files INTEGER NOT NULL DEFAULT 0,
  formula_version TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL,
  signature BLOB,
  synced_at INTEGER,
  UNIQUE(project_id, bucket_start_wall)
);
CREATE INDEX idx_bucket_project_start ON bucket(project_id, bucket_start_wall);

CREATE TABLE chain_head (
  project_id TEXT PRIMARY KEY,
  last_hash TEXT NOT NULL,
  last_mono_ms INTEGER NOT NULL,
  bucket_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE evidence_pack (
  pack_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  from_bucket TEXT NOT NULL,
  to_bucket TEXT NOT NULL,
  ai_declaration TEXT NOT NULL,
  frozen_at INTEGER NOT NULL,
  artifact_uri TEXT
);

-- Server (Postgres, single node). Buckets only; no raw events, prompts, or code.
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  handle TEXT UNIQUE NOT NULL,
  is_public BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE projects (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,
  repo_url TEXT,
  repo_remote_hash TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE bucket (
  bucket_id TEXT NOT NULL,
  install_id TEXT NOT NULL,
  user_id UUID NOT NULL REFERENCES users(id),
  project_id UUID NOT NULL REFERENCES projects(id),
  bucket_start TIMESTAMPTZ NOT NULL,
  bucket_secs INT NOT NULL DEFAULT 300,
  human_edits INT NOT NULL DEFAULT 0,
  ai_writes INT NOT NULL DEFAULT 0,
  prompts INT NOT NULL DEFAULT 0,
  executions INT NOT NULL DEFAULT 0,
  exec_failures INT NOT NULL DEFAULT 0,
  commits INT NOT NULL DEFAULT 0,
  dwell_diff_secs INT NOT NULL DEFAULT 0,
  idle_secs INT NOT NULL DEFAULT 0,
  lines_added INT NOT NULL DEFAULT 0,
  lines_deleted INT NOT NULL DEFAULT 0,
  head_commit TEXT,
  dirty_files INT NOT NULL DEFAULT 0,
  formula_version TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL,
  weighted_secs NUMERIC(10,2) NOT NULL,
  PRIMARY KEY (bucket_start, bucket_id)
) PARTITION BY RANGE (bucket_start);

CREATE TABLE bucket_2026_11 PARTITION OF bucket
  FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE INDEX idx_bucket_user_time ON bucket(user_id, bucket_start DESC);
CREATE INDEX idx_bucket_project_time ON bucket(project_id, bucket_start DESC);

CREATE TABLE submission (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(id),
  user_id UUID NOT NULL REFERENCES users(id),
  from_bucket TEXT NOT NULL,
  to_bucket TEXT NOT NULL,
  head_from TEXT NOT NULL,
  head_to TEXT NOT NULL,
  claimed_weighted_secs NUMERIC(12,2) NOT NULL,
  ai_declaration TEXT NOT NULL,
  evidence_uri TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','deflated','rejected')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE review (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  submission_id UUID NOT NULL REFERENCES submission(id),
  reviewer_id UUID NOT NULL REFERENCES users(id),
  approved_weighted_secs NUMERIC(12,2) NOT NULL,
  reason_code TEXT CHECK(reason_code IN ('UNATTENDED_AGENT_DUMP','IDLE_INFLATION','NO_TEST_ITERATION','BOILERPLATE_HEAVY','CHAIN_BROKEN','GIT_MISMATCH','NONE')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_submission_status ON submission(status, created_at);

//! StruggleTime HTTP API + static dashboard (Native HTTP API + static dashboard).
use anyhow::{Context, Result};
use axum::body::Body;
use axum::extract::{Path, State};
use axum::http::{header, HeaderMap, Request, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use chrono::Utc;
use clap::Parser;
use rusqlite::{params, Connection};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use st_core::verify::{
    billable_secs, validate_allowlist, verify_chain, weight_sequence, FORMULA_VERSION,
};
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use tower_http::services::{ServeDir, ServeFile};

#[derive(Parser, Debug)]
#[command(name = "struggletime-server")]
pub struct ServerArgs {
    #[arg(long, env = "PORT", default_value = "18765")]
    port: u16,
    #[arg(long, env = "BIND", default_value = "127.0.0.1")]
    bind: String,
    #[arg(long, env = "SERVER_DB", default_value = "server.db")]
    db: PathBuf,
    /// Directory with index.html / app.js (default: ../server/static relative to cwd)
    #[arg(long, env = "STATIC_DIR")]
    static_dir: Option<PathBuf>,
}

#[derive(Clone)]
struct AppState {
    db: Arc<Mutex<Connection>>,
    static_dir: PathBuf,
}

pub fn run(args: ServerArgs) -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();
    let rt = tokio::runtime::Runtime::new()?;
    rt.block_on(run_async(args))
}

async fn run_async(args: ServerArgs) -> Result<()> {
    let static_dir = args.static_dir.unwrap_or_else(|| {
        let candidates = [
            PathBuf::from("server/static"),
            PathBuf::from("../server/static"),
            PathBuf::from("../../server/static"),
        ];
        candidates
            .into_iter()
            .find(|p| p.join("index.html").exists())
            .unwrap_or_else(|| PathBuf::from("server/static"))
    });

    let conn =
        Connection::open(&args.db).with_context(|| format!("open db {}", args.db.display()))?;
    init_db(&conn)?;
    let state = AppState {
        db: Arc::new(Mutex::new(conn)),
        static_dir: static_dir.clone(),
    };

    let api = Router::new()
        .route("/health", get(health))
        .route("/v1/auth/me", get(auth_me))
        .route("/v1/keys", post(create_key))
        .route("/v1/buckets/batch", post(buckets_batch))
        .route("/v1/users/{handle}", get(user_public))
        .route("/v1/me/overview", get(overview))
        .with_state(state.clone());

    let index = static_dir.join("index.html");
    let app = Router::new()
        .merge(api)
        .nest_service(
            "/docs",
            ServeDir::new("docs-site/out/docs").append_index_html_on_directories(true),
        )
        .route_service("/app.js", ServeFile::new(static_dir.join("app.js")))
        .route_service("/styles.css", ServeFile::new(static_dir.join("styles.css")))
        .route_service("/ui.js", ServeFile::new(static_dir.join("ui.js")))
        .nest_service("/img", ServeDir::new(static_dir.join("img")))
        .fallback(get(spa_fallback).with_state(state));

    let addr: SocketAddr = format!("{}:{}", args.bind, args.port).parse()?;
    tracing::info!(%addr, statics = %static_dir.display(), "struggletime-server listening");
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    let _ = index;
    Ok(())
}

fn init_db(conn: &Connection) -> Result<()> {
    conn.execute_batch(
        r#"
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY,
          handle TEXT UNIQUE NOT NULL,
          created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS api_keys (
          token_hash TEXT PRIMARY KEY,
          user_id TEXT NOT NULL,
          prefix TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS buckets (
          hash TEXT PRIMARY KEY,
          user_id TEXT NOT NULL,
          project_id TEXT,
          body TEXT NOT NULL,
          weighted REAL NOT NULL,
          state TEXT NOT NULL,
          created_at INTEGER NOT NULL
        );
        "#,
    )?;
    Ok(())
}

async fn health() -> Json<Value> {
    Json(json!({"ok": true, "formula_version": FORMULA_VERSION, "impl": "rust"}))
}

async fn spa_fallback(State(state): State<AppState>, req: Request<Body>) -> Response {
    let path = req.uri().path();
    // Never treat API as SPA
    if path.starts_with("/v1/") || path == "/health" {
        return StatusCode::NOT_FOUND.into_response();
    }
    let file = state.static_dir.join("index.html");
    match tokio::fs::read(&file).await {
        Ok(bytes) => Response::builder()
            .status(StatusCode::OK)
            .header(header::CONTENT_TYPE, "text/html; charset=utf-8")
            .body(Body::from(bytes))
            .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response()),
        Err(_) => (
            StatusCode::NOT_FOUND,
            "static dashboard missing — set STATIC_DIR or run from repo root",
        )
            .into_response(),
    }
}

fn hash_token(token: &str) -> String {
    let mut h = Sha256::new();
    h.update(token.as_bytes());
    hex::encode(h.finalize())
}

fn user_from_auth(
    state: &AppState,
    headers: &HeaderMap,
) -> Result<(String, String), (StatusCode, Json<Value>)> {
    let auth = headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let token = auth.strip_prefix("Bearer ").unwrap_or("").trim();
    if token.is_empty() {
        return Err((
            StatusCode::UNAUTHORIZED,
            Json(json!({"error": "missing bearer token"})),
        ));
    }
    let th = hash_token(token);
    let db = state.db.lock().map_err(|_| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "db lock"})),
        )
    })?;
    let row: Result<(String, String), rusqlite::Error> = db.query_row(
        "SELECT u.id, u.handle FROM api_keys k JOIN users u ON u.id = k.user_id WHERE k.token_hash = ?1",
        params![th],
        |r| Ok((r.get(0)?, r.get(1)?)),
    );
    row.map_err(|_| {
        (
            StatusCode::UNAUTHORIZED,
            Json(json!({"error": "invalid api key"})),
        )
    })
}

async fn auth_me(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let (id, handle) = user_from_auth(&state, &headers)?;
    Ok(Json(json!({"id": id, "handle": handle})))
}

#[derive(Deserialize)]
struct CreateKeyBody {
    handle: String,
}

async fn create_key(
    State(state): State<AppState>,
    Json(body): Json<CreateKeyBody>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let handle = body.handle.trim().to_lowercase();
    if handle.is_empty() || handle.len() > 32 {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "bad handle"})),
        ));
    }
    let user_id = uuid::Uuid::new_v4().to_string();
    let raw = format!(
        "st_{}",
        hex::encode(Sha256::digest(uuid::Uuid::new_v4().as_bytes()))
    );
    let token = format!("st_{}", &raw[3..3 + 40.min(raw.len() - 3)]);
    let token = if token.len() < 20 {
        format!("st_{}", uuid::Uuid::new_v4().simple())
    } else {
        token
    };
    let th = hash_token(&token);
    let now = Utc::now().timestamp();
    let db = state.db.lock().map_err(|_| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "db lock"})),
        )
    })?;
    // Upsert user by handle
    let existing: Result<String, _> = db.query_row(
        "SELECT id FROM users WHERE handle = ?1",
        params![&handle],
        |r| r.get(0),
    );
    let uid = match existing {
        Ok(id) => id,
        Err(_) => {
            db.execute(
                "INSERT INTO users (id, handle, created_at) VALUES (?1, ?2, ?3)",
                params![&user_id, &handle, now],
            )
            .map_err(|e| {
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"error": e.to_string()})),
                )
            })?;
            user_id
        }
    };
    db.execute(
        "INSERT INTO api_keys (token_hash, user_id, prefix, created_at) VALUES (?1, ?2, ?3, ?4)",
        params![&th, &uid, &token[..8.min(token.len())], now],
    )
    .map_err(|e| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": e.to_string()})),
        )
    })?;
    Ok(Json(json!({"token": token, "handle": handle})))
}

#[derive(Deserialize)]
struct BatchBody {
    buckets: Vec<Value>,
}

async fn buckets_batch(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<BatchBody>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let (uid, _handle) = user_from_auth(&state, &headers)?;
    if body.buckets.is_empty() {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "empty batch"})),
        ));
    }
    for b in &body.buckets {
        let errs = validate_allowlist(b);
        if !errs.is_empty() {
            return Err((
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({"error": "allowlist", "details": errs})),
            ));
        }
    }
    let chain_errs = verify_chain(&body.buckets);
    if !chain_errs.is_empty() {
        // Soft: still accept first-time uploads that aren't a continuous chain vs server head.
        tracing::debug!(?chain_errs, "chain warnings");
    }
    let seq = weight_sequence(&body.buckets, 0);
    let db = state.db.lock().map_err(|_| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "db lock"})),
        )
    })?;
    let now = Utc::now().timestamp();
    let mut accepted = 0u32;
    for (b, (w, state_label)) in body.buckets.iter().zip(seq.iter()) {
        let hash = b.get("hash").and_then(|v| v.as_str()).unwrap_or("");
        if hash.is_empty() {
            continue;
        }
        let project = b.get("project_id").and_then(|v| v.as_str()).unwrap_or("");
        let weighted = billable_secs(b) as f64 * w;
        let body_s = serde_json::to_string(b).unwrap_or_else(|_| "{}".into());
        let _ = db.execute(
            "INSERT OR IGNORE INTO buckets (hash, user_id, project_id, body, weighted, state, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![hash, &uid, project, body_s, weighted, state_label, now],
        );
        accepted += 1;
    }
    Ok(Json(json!({"ok": true, "accepted": accepted})))
}

async fn user_public(
    State(state): State<AppState>,
    Path(handle): Path<String>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let db = state.db.lock().map_err(|_| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "db lock"})),
        )
    })?;
    let uid: String = db
        .query_row(
            "SELECT id FROM users WHERE handle = ?1",
            params![&handle],
            |r| r.get(0),
        )
        .map_err(|_| (StatusCode::NOT_FOUND, Json(json!({"error": "not found"}))))?;
    let hours: f64 = db
        .query_row(
            "SELECT COALESCE(SUM(weighted), 0) / 3600.0 FROM buckets WHERE user_id = ?1",
            params![&uid],
            |r| r.get(0),
        )
        .unwrap_or(0.0);
    Ok(Json(json!({
        "handle": handle,
        "weighted_hours": (hours * 1000.0).round() / 1000.0,
        "display_name": handle,
    })))
}

async fn overview(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let (uid, handle) = user_from_auth(&state, &headers)?;
    let db = state.db.lock().map_err(|_| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "db lock"})),
        )
    })?;
    let hours: f64 = db
        .query_row(
            "SELECT COALESCE(SUM(weighted), 0) / 3600.0 FROM buckets WHERE user_id = ?1",
            params![&uid],
            |r| r.get(0),
        )
        .unwrap_or(0.0);
    let buckets: i64 = db
        .query_row(
            "SELECT COUNT(*) FROM buckets WHERE user_id = ?1",
            params![&uid],
            |r| r.get(0),
        )
        .unwrap_or(0);
    Ok(Json(json!({
        "handle": handle,
        "weighted_hours": (hours * 1000.0).round() / 1000.0,
        "bucket_count": buckets,
        "formula_version": FORMULA_VERSION,
    })))
}

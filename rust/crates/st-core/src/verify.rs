//! Causal weighting + chain verification (shared weighting + chain verification).
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

pub const GENESIS: &str = "0000000000000000000000000000000000000000000000000000000000000000";
pub const FORMULA_VERSION: &str = "v0.4.0-alpha";
pub const SILENT_FULL_SECS: i64 = 300;
pub const SILENT_ZERO_SECS: i64 = 720;

fn counts(bucket: &Value) -> Map<String, Value> {
    bucket
        .get("counts")
        .and_then(|c| c.as_object())
        .cloned()
        .unwrap_or_default()
}

fn ci(c: &Map<String, Value>, k: &str) -> i64 {
    c.get(k).and_then(|v| v.as_i64()).unwrap_or(0)
}

fn has_activity(c: &Map<String, Value>) -> bool {
    [
        "human_edits",
        "ai_writes",
        "prompts",
        "executions",
        "commits",
        "same_file_reedit_count",
        "atomic_agent_writes",
        "git_plumbing_pulses",
        "build_test_cycles",
        "cache_fail_to_pass",
        "test_fail_to_pass_transitions",
    ]
    .iter()
    .any(|k| ci(c, k) > 0)
}

pub fn is_silent(bucket: &Value) -> bool {
    !has_activity(&counts(bucket))
}

pub fn decay_weight(silent_secs: i64) -> f64 {
    if silent_secs <= SILENT_FULL_SECS {
        return 0.70;
    }
    if silent_secs >= SILENT_ZERO_SECS {
        return 0.0;
    }
    ((0.70 * (SILENT_ZERO_SECS - silent_secs) as f64)
        / (SILENT_ZERO_SECS - SILENT_FULL_SECS) as f64
        * 10000.0)
        .round()
        / 10000.0
}

fn idle_decay_factor(idle_secs: i64) -> f64 {
    if idle_secs <= SILENT_FULL_SECS {
        return 1.0;
    }
    if idle_secs >= SILENT_ZERO_SECS {
        return 0.0;
    }
    (((SILENT_ZERO_SECS - idle_secs) as f64) / (SILENT_ZERO_SECS - SILENT_FULL_SECS) as f64
        * 10000.0)
        .round()
        / 10000.0
}

pub fn billable_secs(bucket: &Value) -> i64 {
    let secs = bucket
        .get("bucket_secs")
        .and_then(|v| v.as_i64())
        .unwrap_or(0)
        .max(0);
    if is_silent(bucket) {
        return secs;
    }
    let c = counts(bucket);
    match c.get("active_secs").and_then(|v| v.as_i64()) {
        Some(active) if active > 0 => secs.min(active).max(0),
        _ => secs,
    }
}

pub fn is_ai_struggle(c: &Map<String, Value>) -> bool {
    let reedit = ci(c, "same_file_reedit_count");
    let retries = ci(c, "ai_retry_loops");
    let fail_pass = ci(c, "test_fail_to_pass_transitions") + ci(c, "cache_fail_to_pass");
    let churn = ci(c, "agent_turn_churn");
    let recovery = ci(c, "human_recovery_edits");
    let failed = {
        let f = ci(c, "failed_exec_count");
        if f != 0 {
            f
        } else {
            ci(c, "exec_failures")
        }
    };
    let ai = ci(c, "ai_writes");
    let prompts = ci(c, "prompts");
    let human = ci(c, "human_edits");
    let atomic = ci(c, "atomic_agent_writes");
    let git_pulses = ci(c, "git_plumbing_pulses");
    let build_cycles = ci(c, "build_test_cycles");
    let reverts = ci(c, "reflog_reverts");

    if fail_pass > 0 {
        return true;
    }
    if failed > 0
        && reedit > 0
        && (ai > 0 || churn > 0 || prompts > 0 || human > 0 || recovery > 0 || atomic > 0)
    {
        return true;
    }
    if failed > 0 && (retries > 0 || recovery > 0) && (reedit > 0 || human > 0 || ai > 0) {
        return true;
    }
    if retries >= 2 && (failed > 0 || recovery > 0 || fail_pass > 0) {
        return true;
    }
    if churn >= 3 && failed > 0 && (reedit > 0 || recovery > 0 || human > 0) {
        return true;
    }
    if recovery > 0 && (ai > 0 || failed > 0) && (reedit > 0 || fail_pass > 0 || retries > 0) {
        return true;
    }
    if reedit >= 2 && (build_cycles >= 1 || git_pulses >= 2 || atomic >= 1) {
        return true;
    }
    if build_cycles >= 2 && (reedit >= 1 || atomic >= 1 || human >= 1 || ai >= 1) {
        return true;
    }
    if atomic >= 2 && reedit >= 2 {
        return true;
    }
    if git_pulses >= 3 && (reedit >= 1 || reverts >= 1 || build_cycles >= 1) {
        return true;
    }
    if reedit >= 2 && (retries >= 1 || recovery >= 1 || ai > 0 || human >= 2) {
        return true;
    }
    if reedit >= 3 && (ai > 0 || prompts > 0 || churn > 0 || failed > 0 || atomic > 0) {
        return true;
    }
    false
}

pub fn weight(bucket: &Value) -> (f64, String) {
    let c = counts(bucket);
    let cats = bucket
        .get("exec_categories")
        .and_then(|v| v.as_object())
        .cloned()
        .unwrap_or_default();
    let followup =
        ci(&c, "human_edits") > 0 || ci(&c, "commits") > 0 || ci(&c, "human_recovery_edits") > 0;
    let steering =
        ci(&c, "prompts") > 0 || ci(&c, "human_edits") > 0 || ci(&c, "agent_turn_churn") > 0;
    let verified_loop = ci(&c, "exec_failures") > 0 && ci(&c, "executions") > 1 && followup;
    let closed = steering && ci(&c, "executions") > 0 && followup;
    let formatterish = ci(&cats, "formatter") > 0;
    let ai_struggle = is_ai_struggle(&c);

    let open_loop = ci(&c, "ai_writes") > 0
        && ci(&c, "human_edits") == 0
        && ci(&c, "human_recovery_edits") == 0
        && ci(&c, "executions") == 0
        && ci(&c, "dwell_diff_secs") < 30
        && !formatterish
        && !ai_struggle
        && ci(&c, "same_file_reedit_count") == 0
        && ci(&c, "ai_retry_loops") == 0
        && ci(&c, "git_plumbing_pulses") == 0
        && ci(&c, "build_test_cycles") == 0
        && ci(&c, "atomic_agent_writes") == 0
        && ci(&c, "cache_fail_to_pass") == 0;

    let ai_stare = ci(&c, "ai_writes") > 0
        && ci(&c, "human_edits") == 0
        && ci(&c, "executions") == 0
        && ci(&c, "idle_secs") >= SILENT_FULL_SECS
        && !ai_struggle;

    let (mut w, mut state) = if ai_struggle
        || (verified_loop
            && (ci(&c, "ai_writes") > 0 || ci(&c, "prompts") > 0 || ci(&c, "agent_turn_churn") > 0))
    {
        (1.0, "AI_STRUGGLE".to_string())
    } else if verified_loop {
        (1.0, "STRUGGLE_LOOP".to_string())
    } else if closed {
        (1.0, "CLOSED_LOOP".to_string())
    } else if ci(&c, "ai_writes") > 0 && ci(&c, "dwell_diff_secs") >= 30 && followup {
        (0.85, "AI_REVIEWED".to_string())
    } else if open_loop || ai_stare {
        (0.20, "OPEN_LOOP_AGENT_DUMP".to_string())
    } else if ci(&c, "human_edits") > 0 {
        (0.70, "HUMAN_UNVERIFIED".to_string())
    } else if ci(&c, "ai_writes") > 0 {
        (0.25, "AI_UNREVIEWED".to_string())
    } else if ci(&c, "executions") > 0 {
        (0.50, "EXEC_ONLY".to_string())
    } else if is_silent(bucket) {
        (0.70, "SILENT_DEBUG".to_string())
    } else {
        (0.0, "FALLBACK".to_string())
    };

    if !matches!(
        state.as_str(),
        "SILENT_DEBUG" | "FALLBACK" | "AI_STRUGGLE" | "STRUGGLE_LOOP"
    ) && ci(&c, "idle_secs") > SILENT_FULL_SECS
    {
        let factor = idle_decay_factor(ci(&c, "idle_secs"));
        w = (w * factor * 10000.0).round() / 10000.0;
        if factor <= 0.0 {
            state = "IDLE".to_string();
        } else if factor < 1.0 {
            state = format!("{state}_DECAY");
        }
    }
    (w, state)
}

pub fn weight_sequence(buckets: &[Value], initial_silent_secs: i64) -> Vec<(f64, String)> {
    let mut out = Vec::with_capacity(buckets.len());
    let mut silent = initial_silent_secs;
    for b in buckets {
        if is_silent(b) {
            silent += b.get("bucket_secs").and_then(|v| v.as_i64()).unwrap_or(0);
            out.push((decay_weight(silent), "SILENT_DEBUG".to_string()));
        } else {
            silent = 0;
            out.push(weight(b));
        }
    }
    out
}

pub fn friction(bucket: &Value) -> f64 {
    let c = counts(bucket);
    let struggle = 0.12 * (ci(&c, "same_file_reedit_count").min(5) as f64) / 5.0
        + 0.12 * (ci(&c, "ai_retry_loops").min(4) as f64) / 4.0
        + 0.12
            * ((ci(&c, "test_fail_to_pass_transitions") + ci(&c, "cache_fail_to_pass")).min(3)
                as f64)
            / 3.0
        + 0.08 * (ci(&c, "human_recovery_edits").min(4) as f64) / 4.0
        + 0.08 * (ci(&c, "agent_turn_churn").min(5) as f64) / 5.0
        + 0.08 * (ci(&c, "atomic_agent_writes").min(4) as f64) / 4.0
        + 0.08 * (ci(&c, "git_plumbing_pulses").min(4) as f64) / 4.0
        + 0.08 * (ci(&c, "build_test_cycles").min(4) as f64) / 4.0;
    let failed = {
        let f = ci(&c, "exec_failures");
        if f != 0 {
            f
        } else {
            ci(&c, "failed_exec_count")
        }
    };
    let raw = 0.25 * (failed.min(3) as f64) / 3.0
        + 0.15 * (ci(&c, "executions").min(4) as f64) / 4.0
        + 0.10 * (ci(&c, "human_edits").min(10) as f64) / 10.0
        + 0.10
            * (if ci(&c, "commits") > 0 || ci(&c, "human_edits") > 0 {
                1.0
            } else {
                0.0
            })
            * ((ci(&c, "prompts") + 1).min(3) as f64)
            / 3.0
        + struggle;
    (raw.min(1.0) * 1000.0).round() / 1000.0
}

/// Canonical JSON with sorted keys (compact separators).
fn escape_key_proper(k: &str) -> String {
    serde_json::to_string(k).unwrap_or_else(|_| format!("\"{k}\""))
}

fn sorted_json_py(v: &Value) -> String {
    match v {
        Value::Object(map) => {
            let mut keys: Vec<_> = map.keys().collect();
            keys.sort();
            let parts: Vec<String> = keys
                .into_iter()
                .map(|k| format!("{}:{}", escape_key_proper(k), sorted_json_py(&map[k])))
                .collect();
            format!("{{{}}}", parts.join(","))
        }
        Value::Array(arr) => {
            let parts: Vec<String> = arr.iter().map(sorted_json_py).collect();
            format!("[{}]", parts.join(","))
        }
        other => serde_json::to_string(other).unwrap_or_else(|_| "null".into()),
    }
}

pub fn bucket_hash(bucket: &Value) -> String {
    let mut b = bucket.clone();
    if let Some(obj) = b.as_object_mut() {
        obj.remove("hash");
        obj.remove("signature");
    }
    let prev = bucket
        .get("prev_hash")
        .and_then(|v| v.as_str())
        .unwrap_or(GENESIS);
    let mut h = Sha256::new();
    h.update(sorted_json_py(&b).as_bytes());
    h.update(prev.as_bytes());
    hex::encode(h.finalize())
}

pub fn verify_chain(buckets: &[Value]) -> Vec<String> {
    let mut errors = Vec::new();
    let mut prev = GENESIS.to_string();
    let mut last_mono: Option<i64> = None;
    let mut last_wall: Option<i64> = None;
    for (i, b) in buckets.iter().enumerate() {
        let ph = b.get("prev_hash").and_then(|v| v.as_str()).unwrap_or("");
        if ph != prev {
            errors.push(format!("bucket {i}: prev_hash mismatch (chain broken)"));
        }
        let expected = bucket_hash(b);
        let actual = b.get("hash").and_then(|v| v.as_str()).unwrap_or("");
        if expected != actual {
            errors.push(format!("bucket {i}: hash mismatch (tampered)"));
        }
        if let Some(mono) = b.get("mono_delta_ms").and_then(|v| v.as_i64()) {
            if last_mono.is_some() && mono < 0 {
                errors.push(format!("bucket {i}: monotonic clock regression"));
            }
            last_mono = Some(mono);
        }
        if let Some(wall) = b.get("bucket_start_wall").and_then(|v| v.as_i64()) {
            if let Some(lw) = last_wall {
                let jump = wall - lw;
                if jump < -60 {
                    errors.push(format!("bucket {i}: wall clock jumped backward ({jump}s)"));
                } else if jump > 6 * 3600 {
                    errors.push(format!("bucket {i}: wall clock jumped forward ({jump}s)"));
                }
            }
            last_wall = Some(wall);
        }
        if let Some(h) = b.get("hash").and_then(|v| v.as_str()) {
            prev = h.to_string();
        }
    }
    errors
}

pub fn validate_allowlist(bucket: &Value) -> Vec<String> {
    use crate::agent_events::FORBIDDEN_KEYS;

    let mut errs = Vec::new();
    collect_forbidden_paths(bucket, "top", &mut errs);
    if let Some(files) = bucket.get("files").and_then(|v| v.as_array()) {
        if files.len() > 50 {
            errs.push("files: max 50 per bucket".into());
        }
        for (i, f) in files.iter().enumerate() {
            if let Some(fid) = f.get("file_id").and_then(|x| x.as_str()) {
                if fid.len() != 16 || !fid.chars().all(|c| c.is_ascii_hexdigit()) {
                    errs.push(format!(
                        "files[{i}].file_id: must be 16 hex chars (salted id)"
                    ));
                }
            }
            // Absolute / home-looking strings must never appear as values under files.
            if let Some(obj) = f.as_object() {
                for (k, v) in obj {
                    if let Some(s) = v.as_str() {
                        if (s.starts_with('/') || s.contains(":\\") || s.contains("\\\\"))
                            && *k != "file_id"
                            && *k != "op"
                            && *k != "source"
                            && *k != "editor"
                            && *k != "agent"
                            && *k != "language"
                        {
                            errs.push(format!("files[{i}].{k}: looks like an unhashed path"));
                        }
                    }
                }
            }
        }
    }
    // Keep the historical FORBIDDEN list name in error text for ops familiarity.
    let _ = FORBIDDEN_KEYS;
    errs
}

fn collect_forbidden_paths(v: &Value, prefix: &str, errs: &mut Vec<String>) {
    use crate::agent_events::FORBIDDEN_KEYS;
    match v {
        Value::Object(map) => {
            for (k, child) in map {
                if FORBIDDEN_KEYS.contains(&k.as_str()) {
                    errs.push(format!(
                        "{prefix}.{k}: forbidden (raw content can never be sent)"
                    ));
                }
                collect_forbidden_paths(child, &format!("{prefix}.{k}"), errs);
            }
        }
        Value::Array(items) => {
            for (i, child) in items.iter().enumerate() {
                collect_forbidden_paths(child, &format!("{prefix}[{i}]"), errs);
            }
        }
        _ => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn struggle_loop_full_weight() {
        let b = json!({
            "bucket_secs": 300,
            "counts": {
                "exec_failures": 1,
                "executions": 3,
                "human_edits": 2,
                "ai_writes": 1,
                "active_secs": 200
            }
        });
        let (w, state) = weight(&b);
        assert!((w - 1.0).abs() < 1e-9);
        assert!(state == "AI_STRUGGLE" || state == "STRUGGLE_LOOP");
    }

    #[test]
    fn open_loop_deflates() {
        let b = json!({
            "bucket_secs": 300,
            "counts": {
                "ai_writes": 5,
                "human_edits": 0,
                "executions": 0,
                "dwell_diff_secs": 5
            }
        });
        let (w, state) = weight(&b);
        assert!((w - 0.20).abs() < 1e-9);
        assert_eq!(state, "OPEN_LOOP_AGENT_DUMP");
    }

    #[test]
    fn chain_hashes_link() {
        let mut a = json!({
            "bucket_id": "a",
            "bucket_secs": 60,
            "bucket_start_wall": 1000,
            "mono_delta_ms": 0,
            "counts": {"human_edits": 1, "active_secs": 30},
            "prev_hash": GENESIS
        });
        let ha = bucket_hash(&a);
        a.as_object_mut().unwrap().insert("hash".into(), json!(ha));

        let mut b = json!({
            "bucket_id": "b",
            "bucket_secs": 60,
            "bucket_start_wall": 1060,
            "mono_delta_ms": 60000,
            "counts": {"human_edits": 1, "active_secs": 30},
            "prev_hash": ha
        });
        let hb = bucket_hash(&b);
        b.as_object_mut().unwrap().insert("hash".into(), json!(hb));

        let errs = verify_chain(&[a, b]);
        assert!(errs.is_empty(), "{errs:?}");
    }

    #[test]
    fn allowlist_rejects_nested_prompt_and_path() {
        let b = json!({
            "bucket_id": "x",
            "counts": {"human_edits": 1, "meta": {"prompt_text": "leak"}},
            "files": [{"file_id": "aaaaaaaaaaaaaaaa", "path": "/tmp/x.rs"}]
        });
        let errs = validate_allowlist(&b);
        assert!(errs.iter().any(|e| e.contains("prompt_text")), "{errs:?}");
        assert!(errs.iter().any(|e| e.contains("path")), "{errs:?}");
    }

    #[test]
    fn allowlist_rejects_bad_file_id() {
        let b = json!({
            "bucket_id": "x",
            "counts": {"human_edits": 1},
            "files": [{"file_id": "not-a-salted-id", "op": "modified"}]
        });
        let errs = validate_allowlist(&b);
        assert!(errs.iter().any(|e| e.contains("file_id")), "{errs:?}");
    }
}

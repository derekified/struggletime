//! Adapter / subagent event ingest (counts only).
//! Never accept raw paths, prompts, argv, transcripts, or file contents.

use serde_json::Value;
use std::collections::HashSet;

/// Keys that must never appear anywhere in adapter / bucket JSON.
pub const FORBIDDEN_KEYS: &[&str] = &[
    "content",
    "text",
    "code",
    "prompt",
    "prompt_text",
    "message",
    "command",
    "args",
    "argv",
    "keystrokes",
    "screenshot",
    "image",
    "path",
    "filepath",
    "file_path",
    "raw",
    "transcript",
    "cwd",
    "workspace",
    "body",
    "diff",
    "patch",
];

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct AgentEventDelta {
    pub human_edits: i64,
    pub ai_writes: i64,
    pub prompts: i64,
    pub executions: i64,
    pub exec_failures: i64,
    pub project_id: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct AgentDrainStats {
    pub lines: usize,
    pub accepted: usize,
    pub rejected_forbidden: usize,
    pub rejected_dup: usize,
    pub rejected_malformed: usize,
    /// Wall-tick credit for this drain (0 or 1). Never scales with event fan-out.
    pub activity_tick: i64,
}

/// True if `v` (recursively) contains any forbidden object key.
pub fn json_contains_forbidden_keys(v: &Value) -> bool {
    match v {
        Value::Object(map) => {
            for (k, child) in map {
                if FORBIDDEN_KEYS.contains(&k.as_str()) {
                    return true;
                }
                if json_contains_forbidden_keys(child) {
                    return true;
                }
            }
            false
        }
        Value::Array(items) => items.iter().any(json_contains_forbidden_keys),
        _ => false,
    }
}

/// Classify one allowlisted adapter line into count deltas. `None` = reject.
pub fn classify_agent_event(v: &Value) -> Option<AgentEventDelta> {
    if !v.is_object() {
        return None;
    }
    if json_contains_forbidden_keys(v) {
        return None;
    }
    let et = v.get("event_type").and_then(|x| x.as_str()).unwrap_or("");
    let cat = v.get("category").and_then(|x| x.as_str()).unwrap_or("");
    let project_id = v
        .get("project_id")
        .and_then(|x| x.as_str())
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string());

    let mut d = AgentEventDelta {
        project_id,
        ..Default::default()
    };

    match et {
        "edit" | "save" | "modified" | "created" | "focus" => d.human_edits += 1,
        "tool" | "start" | "stop" => {
            d.ai_writes += 1;
            if et == "tool" || cat == "ai_cli" {
                d.executions += 1;
            }
        }
        "prompt" => d.prompts += 1,
        "exec" | "play" => {
            d.executions += 1;
            if v.get("exit_code").and_then(|x| x.as_i64()).unwrap_or(0) != 0 {
                d.exec_failures += 1;
            }
        }
        _ => {
            if cat == "ai_cli" {
                d.ai_writes += 1;
            } else if cat == "editor" {
                d.human_edits += 1;
            } else if et.is_empty() && cat.is_empty() {
                return None;
            }
        }
    }
    Some(d)
}

/// Parse a JSONL drain: dedupe identical lines, reject privacy traps, aggregate deltas.
///
/// `activity_tick` is at most 1 so a subagent retry storm cannot inflate
/// `bucket_secs` / `active_secs` beyond the ~1s poll window.
pub fn drain_agent_events_jsonl(raw: &str) -> (Vec<AgentEventDelta>, AgentDrainStats) {
    let mut stats = AgentDrainStats::default();
    let mut seen = HashSet::new();
    let mut out = Vec::new();

    for line in raw.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        stats.lines += 1;
        if !seen.insert(line.to_string()) {
            stats.rejected_dup += 1;
            continue;
        }
        let Ok(v) = serde_json::from_str::<Value>(line) else {
            stats.rejected_malformed += 1;
            continue;
        };
        if json_contains_forbidden_keys(&v) {
            stats.rejected_forbidden += 1;
            continue;
        }
        match classify_agent_event(&v) {
            Some(d) => {
                stats.accepted += 1;
                out.push(d);
            }
            None => stats.rejected_malformed += 1,
        }
    }
    if stats.accepted > 0 {
        stats.activity_tick = 1;
    }
    (out, stats)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn rejects_top_level_prompt_trap() {
        let v = json!({"event_type": "tool", "prompt_text": "rewrite auth"});
        assert!(json_contains_forbidden_keys(&v));
        assert!(classify_agent_event(&v).is_none());
    }

    #[test]
    fn rejects_nested_path_and_argv() {
        let v = json!({
            "event_type": "exec",
            "exit_code": 1,
            "meta": {"cwd": "/tmp/secret", "argv": ["cargo", "test"]}
        });
        assert!(json_contains_forbidden_keys(&v));
        assert!(classify_agent_event(&v).is_none());
    }

    #[test]
    fn accepts_allowlisted_tool_event() {
        let v = json!({
            "event_type": "tool",
            "category": "ai_cli",
            "editor": "cursor",
            "files_touched_count": 2,
            "project_id": "proj-a"
        });
        let d = classify_agent_event(&v).expect("ok");
        assert_eq!(d.ai_writes, 1);
        assert_eq!(d.executions, 1);
        assert_eq!(d.project_id.as_deref(), Some("proj-a"));
    }

    #[test]
    fn retry_storm_dedupes_and_caps_activity_tick() {
        let mut lines = Vec::new();
        for i in 0..200 {
            lines.push(format!(
                r#"{{"event_type":"tool","category":"ai_cli","n":{i}}}"#
            ));
        }
        // Exact duplicates of first line
        for _ in 0..50 {
            lines.push(lines[0].clone());
        }
        let raw = lines.join("\n");
        let (deltas, stats) = drain_agent_events_jsonl(&raw);
        assert_eq!(stats.accepted, 200);
        assert_eq!(stats.rejected_dup, 50);
        assert_eq!(stats.activity_tick, 1);
        assert_eq!(deltas.iter().map(|d| d.ai_writes).sum::<i64>(), 200);
    }

    #[test]
    fn parallel_fanout_preserves_project_ids() {
        let raw = r#"
{"event_type":"edit","project_id":"a","files_touched_count":1}
{"event_type":"tool","category":"ai_cli","project_id":"b"}
{"event_type":"exec","exit_code":0,"project_id":"a"}
"#;
        let (deltas, stats) = drain_agent_events_jsonl(raw);
        assert_eq!(stats.accepted, 3);
        assert_eq!(stats.activity_tick, 1);
        let a: i64 = deltas
            .iter()
            .filter(|d| d.project_id.as_deref() == Some("a"))
            .map(|d| d.human_edits + d.executions)
            .sum();
        let b: i64 = deltas
            .iter()
            .filter(|d| d.project_id.as_deref() == Some("b"))
            .map(|d| d.ai_writes)
            .sum();
        assert_eq!(a, 2);
        assert_eq!(b, 1);
    }

    #[test]
    fn privacy_trap_batch_all_rejected() {
        let raw = r#"
{"event_type":"tool","path":"/Users/me/secret.rs"}
{"event_type":"prompt","transcript":"user: please hack"}
{"event_type":"exec","command":"rm -rf /","args":["-rf","/"]}
{"event_type":"edit","meta":{"file_path":"src/main.rs","text":"fn main(){}"}}
"#;
        let (deltas, stats) = drain_agent_events_jsonl(raw);
        assert!(deltas.is_empty());
        assert_eq!(stats.rejected_forbidden, 4);
        assert_eq!(stats.activity_tick, 0);
    }
}

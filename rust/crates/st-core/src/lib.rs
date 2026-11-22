//! Shared StruggleTime core: config, scoring (verify port), ledger, privacy helpers.
//! Never log or serialize raw prompts, paths, argv, or file contents.

pub mod agent_events;
pub mod config;
pub mod ledger;
pub mod privacy;
pub mod verify;

pub use agent_events::{
    classify_agent_event, drain_agent_events_jsonl, json_contains_forbidden_keys, AgentDrainStats,
    AgentEventDelta, FORBIDDEN_KEYS,
};
pub use config::{load_settings, normalize_server, save_settings, Settings, DEFAULT_SERVER};
pub use ledger::{Ledger, LedgerEntry};
pub use privacy::anonymized_file_id;
pub use verify::{
    billable_secs, bucket_hash, decay_weight, friction, validate_allowlist, verify_chain, weight,
    weight_sequence, FORMULA_VERSION, GENESIS,
};

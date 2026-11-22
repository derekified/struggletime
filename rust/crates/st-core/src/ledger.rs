use crate::verify::{bucket_hash, GENESIS};
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct Ledger {
    #[serde(default)]
    pub entries: Vec<Value>,
    #[serde(default)]
    pub pending: Vec<Value>,
    #[serde(default)]
    pub salt: String,
    #[serde(default)]
    pub chain_head: String,
}

#[derive(Debug, Clone)]
pub struct LedgerEntry;

impl Ledger {
    pub fn path_for_repo(repo: &Path) -> PathBuf {
        repo.join(".aitime-ledger.json")
    }

    pub fn load(path: &Path) -> Result<Self> {
        if !path.exists() {
            return Ok(Self {
                salt: uuid::Uuid::new_v4().to_string(),
                chain_head: GENESIS.to_string(),
                ..Default::default()
            });
        }
        let raw = fs::read_to_string(path).with_context(|| format!("read {}", path.display()))?;
        let mut led: Self = serde_json::from_str(&raw).unwrap_or_default();
        if led.salt.is_empty() {
            led.salt = uuid::Uuid::new_v4().to_string();
        }
        if led.chain_head.is_empty() {
            led.chain_head = led
                .entries
                .last()
                .and_then(|e| e.get("hash"))
                .and_then(|v| v.as_str())
                .unwrap_or(GENESIS)
                .to_string();
        }
        Ok(led)
    }

    pub fn save(&self, path: &Path) -> Result<()> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(path, serde_json::to_string_pretty(self)?)?;
        Ok(())
    }

    pub fn append_sealed(&mut self, mut bucket: Value) -> Result<&Value> {
        let prev = if self.chain_head.is_empty() {
            GENESIS.to_string()
        } else {
            self.chain_head.clone()
        };
        if let Some(obj) = bucket.as_object_mut() {
            obj.insert("prev_hash".into(), json!(prev));
            obj.remove("hash");
            obj.remove("signature");
        }
        let h = bucket_hash(&bucket);
        if let Some(obj) = bucket.as_object_mut() {
            obj.insert("hash".into(), json!(h.clone()));
        }
        self.chain_head = h;
        self.entries.push(bucket.clone());
        self.pending.push(bucket);
        Ok(self.entries.last().unwrap())
    }

    pub fn ack_pending(&mut self, hashes: &[String]) {
        let set: std::collections::HashSet<&str> = hashes.iter().map(|s| s.as_str()).collect();
        self.pending
            .retain(|b| !b.get("hash").and_then(|v| v.as_str()).map(|h| set.contains(h)).unwrap_or(false));
    }
}

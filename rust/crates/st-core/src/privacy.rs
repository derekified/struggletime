use sha2::{Digest, Sha256};

/// Salted file id: `sha256(salt:path)` truncated to 16 hex chars (16 hex chars).
pub fn anonymized_file_id(rel_path: &str, salt: &str) -> String {
    let mut h = Sha256::new();
    h.update(format!("{salt}:{rel_path}").as_bytes());
    let full = hex::encode(h.finalize());
    full.chars().take(16).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn file_id_is_hex16() {
        let id = anonymized_file_id("src/main.rs", "test-salt");
        assert_eq!(id.len(), 16);
        assert!(id.chars().all(|c| c.is_ascii_hexdigit()));
    }
}

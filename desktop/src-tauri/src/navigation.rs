use st_core::{normalize_server, DEFAULT_SERVER};
use url::Url;

#[derive(Debug, Eq, PartialEq)]
pub enum NavigationAction {
    Allow,
    OpenExternal,
    Reject,
}

pub fn dashboard_base() -> Url {
    let configured = st_core::load_settings()
        .map(|s| s.server)
        .unwrap_or_else(|_| DEFAULT_SERVER.into());
    Url::parse(&normalize_server(&configured))
        .or_else(|_| Url::parse(DEFAULT_SERVER))
        .expect("default dashboard URL is valid")
}

pub fn dashboard_page(path: &str) -> Url {
    dashboard_base()
        .join(path)
        .expect("dashboard path is a valid URL")
}

fn same_origin(a: &Url, b: &Url) -> bool {
    a.scheme() == b.scheme()
        && a.host_str() == b.host_str()
        && a.port_or_known_default() == b.port_or_known_default()
}

fn is_oauth_host(host: &str) -> bool {
    host == "slack.com"
        || host.ends_with(".slack.com")
        || host == "github.com"
        || host.ends_with(".github.com")
        || host == "accounts.google.com"
        || host == "login.microsoftonline.com"
}

pub fn navigation_action(base: &Url, candidate: &Url) -> NavigationAction {
    if !matches!(candidate.scheme(), "http" | "https") {
        return NavigationAction::Reject;
    }
    if same_origin(base, candidate) {
        return NavigationAction::Allow;
    }
    if candidate.host_str().is_some_and(is_oauth_host) {
        return NavigationAction::Allow;
    }
    NavigationAction::OpenExternal
}

/// Script is produced only for the exact dashboard origin; never inject a key
/// into an OAuth provider or an unrelated remote page.
pub fn token_bootstrap_script(current: &Url, base: &Url, token: &str) -> Option<String> {
    if token.is_empty() || !same_origin(current, base) {
        return None;
    }
    let token = serde_json::to_string(token).ok()?;
    Some(format!(
        r#"(() => {{
  try {{
    localStorage.setItem("st_token", {token});
    if (location.pathname === "/" || location.pathname === "/login") {{
      location.replace("/home");
    }} else {{
      location.reload();
    }}
  }} catch (_) {{}}
}})();"#
    ))
}

pub fn clear_token_script(current: &Url, base: &Url) -> Option<&'static str> {
    same_origin(current, base).then_some(
        r#"(() => {
  try { localStorage.removeItem("st_token"); location.reload(); } catch (_) {}
})();"#,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dashboard_and_oauth_navigation_are_scoped() {
        let base = Url::parse("https://struggletime.screenseekr.com").unwrap();
        assert_eq!(
            navigation_action(
                &base,
                &Url::parse("https://struggletime.screenseekr.com/home").unwrap()
            ),
            NavigationAction::Allow
        );
        assert_eq!(
            navigation_action(
                &base,
                &Url::parse("https://hackclub.enterprise.slack.com/oauth").unwrap()
            ),
            NavigationAction::Allow
        );
        assert_eq!(
            navigation_action(&base, &Url::parse("https://example.com").unwrap()),
            NavigationAction::OpenExternal
        );
        assert_eq!(
            navigation_action(&base, &Url::parse("file:///etc/passwd").unwrap()),
            NavigationAction::Reject
        );
        assert_eq!(
            navigation_action(
                &base,
                &Url::parse("https://struggletime.screenseekr.com.evil.test").unwrap()
            ),
            NavigationAction::OpenExternal
        );
    }

    #[test]
    fn token_script_is_limited_to_the_dashboard_origin() {
        let base = Url::parse("https://struggletime.screenseekr.com").unwrap();
        assert!(token_bootstrap_script(&base, &base, "st_test").is_some());
        let other = Url::parse("https://slack.com").unwrap();
        assert!(token_bootstrap_script(&other, &base, "st_test").is_none());
        assert!(token_bootstrap_script(&base, &base, "").is_none());
    }
}

# JetBrains IDE adapter

Optional Layer-2 enrichment for IntelliJ IDEA, PyCharm, CLion, WebStorm,
GoLand, RustRover, Rider, and Android Studio.

Layer 1 already maps these process names to `jetbrains` (and related
labels) via `daemon/editors.py`. This scaffold adds typing-burst vs
bulk-write counts plus window focus events to
`~/.aitime/agent_events.jsonl`.

## Scaffold layout

```text
adapters/jetbrains/
  src/main/kotlin/com/struggletime/StruggleTimeStartup.kt
  src/main/resources/META-INF/plugin.xml
```

Build with your usual IntelliJ Platform Gradle plugin, or symlink the
sources into a throwaway plugin module. No document text or absolute
paths are written — only allowlisted counts and labels.

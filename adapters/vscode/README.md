# VS Code / Cursor adapter (thin enrichment)

The daemon already counts this editor via filesystem + shell hooks.
This adapter only adds: human-vs-programmatic edit distinction and focus.

Contract (the whole API): append lines to the daemon exec log /
send `{kind, files_touched}` events to the local daemon:

- `onDidChangeTextDocument` (debounced 2s): if `contentChanges` came from
  an undo/redo or typing, emit `human_edit`; bulk programmatic changes
  emit `ai_write`. Send counts only — never document text.
- `onDidChangeWindowState`: focused/unfocused toggles dwell accounting.

Reference snippet lives in `extension.js`; packaging it as a `.vsix`
is distribution work, not protocol work. Any editor that can write a
JSON line can be an adapter — that is the plugin approach.

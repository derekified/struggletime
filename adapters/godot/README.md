# Godot 4 adapter

Optional Layer-2 enrichment. Layer 1 already labels `godot` via process
signatures when the editor is running over a linked repo.

Enable the plugin under Project → Project Settings → Plugins after copying
`addons/struggletime` into your Godot project (or symlink it).

Emits focus, filesystem/edit heartbeats, and playtest (`play`) counts into
`~/.aitime/agent_events.jsonl`. No script/scene contents or absolute paths.

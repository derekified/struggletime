# Unity Editor adapter

Optional Layer-2 enrichment. Layer 1 already labels `unity` when the
editor process is active over a linked project.

Drop `Editor/StruggleTimeTracker.cs` into any `Assets/../Editor/` folder
(or symlink this tree). On load it hooks focus pulses, asset saves, and
play-mode enter/exit into `~/.aitime/agent_events.jsonl`.

Asset paths are counted, never written. No scene/script contents leave
the editor through this bridge.

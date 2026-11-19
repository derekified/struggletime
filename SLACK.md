# Slack post — copy/paste (replace [repo link])

---

hey everyone :wave: this is what I've made so far for the AI time tracking bounty — it's called **StruggleTime**

the idea: every tracker counts *activity*, but AI makes activity meaningless (an unattended agent is "active" for hours while you're doing something else lol). so instead it counts **causality**:

`(prompt/edit) → (test/build) → (follow-up edit/commit)` = full weight
fail a build → fix it → pass = full weight (that's the struggle loop!!)
agent dumps 15 files and you commit them unread = 0.2x
idle = 0

**what's actually working rn:**
• a passive daemon (filesystem watcher + a tiny shell hook) — no timers, no start/stop, works with any editor/AI tool because it doesn't depend on plugins. adapters for cursor/vscode + claude code are just optional extras on top
• a server with accounts, public profiles, git-linked projects, and a live dashboard (weighted hours, streaks, weekly goal)
• every 5-min bucket is hash-chained + anchored to your git HEAD/diff, so if someone rewrites their history it breaks in review. submitting = freezing an evidence pack + declaring how you used AI, reviewers approve/deflate with a reason code
• privacy: only metadata counts ever leave your laptop. no prompts, no code, no commands, no keystrokes, no screenshots. logged hours stay private, only submitted hours get uploaded
• AGPLv3, zero dependencies, runs on a potato

**proof it works (just tested):**
• 18/18 tests passing
• a real struggle-loop session earned the full 0.25h; an agent-dump session got discounted to 0.033h and auto-flagged
• a tampered bucket gets rejected by the server (hash mismatch)
• load test: 5,000 buckets verified in 0.17s (~31MB RAM) — the full struggletime scale is like 0.5 writes/sec so hosting should land around $40-60/mo

repo: [repo link] — `python3 server/server.py` + `python3 daemon/daemon.py --repo ~/your-project` and you're tracking

**honest wip:** no packaged installers yet (you run it from source), and the weighting formula (v0.1.0-alpha) hasn't been calibrated on real sessions yet

**what I need:** ~20 people willing to record a labelled coding session (screen recording stays on your machine) so I can check the formula agrees with human reviewers before anyone relies on it. if you build with AI a lot and want your struggle to count, pls test it and break it — especially try to fake hours, I want to know :saluting_face:

---

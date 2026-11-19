# slack post — copy/paste (replace [repo link])

---

hey :wave: so i've been building this thing for the ai time tracking bounty and it finally actually works end to end, so here's what i made so far

it's called **struggletime**. the whole idea came from one thing that bugged me: trackers count activity, but ai makes activity meaningless. an agent can look "active" for 3 hours while you're off doing something else. so instead of counting activity, it counts whether you actually closed the loop:

prompt/edit → test/build → follow-up edit/commit = full weight
fail a build → fix it → it passes = full weight (this is my favourite signal lol, failing builds don't lie)
agent dumps a bunch of files and you commit them without looking = 0.2x
idle = 0

what's in it rn:
• a passive daemon — filesystem watcher + a 5-line shell hook. no timers, no start/stop button, you just code and it counts. works with literally any editor/ai tool bc it doesn't rely on plugins (there are little adapters for cursor + claude code but they're optional extras)
• a server with accounts, public profiles, git-linked projects and a dashboard that updates live — weighted hours, streaks, weekly goal, that kind of thing
• every 5-min chunk of time gets hash-chained and tied to your git head + diff size, so if someone tries to rewrite their history before submitting, it just breaks. submitting freezes an evidence pack + you declare how you used ai, and reviewers can approve/deflate with a reason
• privacy stuff i actually cared about: only counts leave your laptop. no prompts, no code, no commands, no keystrokes, no screenshots. logged hours stay private, only stuff you submit gets uploaded
• agplv3, zero dependencies, runs on a potato

i just re-tested the server before posting this, here's the actual output:

```
$ curl localhost:18765/health
{"ok": true}

$ python3 -m unittest discover -s tests
ran 18 tests in 1.379s — ok

struggle-loop session → 0.25 weighted h (full weight, nothing flagged)
agent-dump session → 0.033 weighted h + auto-flagged (ai:human ratio 27)
tampered bucket → 422 hash mismatch, rejected
load test → 5,000 buckets verified in 0.17s, server sitting at ~31mb ram
```

that last one matters bc at full struggletime scale this thing only needs ~0.5 writes/sec, so hosting should be like $40-60/mo, way under the cap

try it: [repo link]
`python3 server/server.py` in one terminal, `python3 daemon/daemon.py --repo ~/your-project` in another, open the dashboard, go build something

honest wip: there's no installer yet so you run it from source, and the weighting formula is v0.1.0-alpha — it has not been checked against real humans yet

which is the part i actually need help with: i need ~20 people to record a normal coding session (recording never leaves your machine, i just need you to label roughly what you were doing) so i can see if the formula agrees with what a human reviewer would say. also pls try to break it / fake hours, i'd genuinely rather find that out from you than from fraud squad

ty for reading this far lol, feedback very welcome

---

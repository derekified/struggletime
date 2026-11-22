# slack post — copy/paste (replace [repo link])

---

hey :wave: so i've been building StruggleTime for Hack Club shipping culture (productive struggle) and it finally actually works end to end, so here's what i made so far

it's called **struggletime**. the whole idea came from one thing that bugged me: trackers count activity, but ai makes activity meaningless. an agent can look "active" for 3 hours while you're off doing something else. so instead of counting activity, it counts whether you actually closed the loop:

prompt/edit → test/build → follow-up edit/commit = full weight
fail a build → fix it → it passes = full weight (this is my favourite signal lol, failing builds don't lie)
agent dumps a bunch of files and you commit them without looking = 0.2x
idle = 0

what's in it rn:
• a passive daemon — filesystem watcher + a 5-line shell hook. no timers, no start/stop button, you just code in your terminal and it counts. works with literally any ai tool bc it doesn't rely on plugins
• a server with accounts, public profiles, git-linked projects and a dashboard that updates live — weighted hours, streaks, weekly goal, that kind of thing
• every 5-min chunk of time gets hash-chained and tied to your git head + diff size, so if someone tries to rewrite their history before submitting, it just breaks. submitting freezes an evidence pack + you declare how you used ai, and reviewers can approve/deflate with a reason
• no screen recording, no keylogging, none of that. instead the evidence is like 25-50kb of pure metadata per session — anonymized file ids, line deltas, build/test exit codes — which reviewers see as a friction timeline overlaid on your git commits (failure → retry → commit, as a shape). silent debugging time decays slowly over 5-12 min instead of dropping to zero. there's a strict schema allowlist so raw text literally cannot be sent (server rejects it), and a local inspect command shows you byte-for-byte what leaves your machine before anything does
• most agent users don't even code in an ide (terminals!!) so the daemon is terminal-first — ide adapters are optional extras, not the product
• agplv3, zero dependencies, runs on a potato

i just re-tested the server before posting this, here's the actual output:

```
$ curl localhost:18765/health
{"ok": true}

$ python3 -m unittest discover -s tests
ran 36 tests in 2.650s — ok

struggle-loop session → 0.25 weighted h (full weight, nothing flagged)
agent-dump session → 0.033 weighted h + auto-flagged (ai:human ratio 27)
tampered bucket → 422 hash mismatch, rejected
load test → 5,000 buckets verified in 0.17s, server sitting at ~31mb ram
```

that last one matters bc at full scale this thing only needs ~0.5 writes/sec, so hosting should be like $40-60/mo, way under the cap

try it: https://github.com/derekified/struggletime
```bash
git clone https://github.com/derekified/struggletime.git
cd struggletime
make server
# other terminal:
sh daemon/install.sh
struggletime-helper start --repo ~/your-project
```

honest wip: weighting is still alpha and needs more real-session calibration. if you want to help, run a normal coding session, label roughly what you were doing (nothing leaves your machine unless you choose to share notes), and tell me where the formula disagrees with a human reviewer. also pls try to break it / fake hours — i'd rather hear that from you than from fraud squad

ty for reading this far, feedback welcome

---

# Daily standup — Tuesday 11:00

Attendees: Alice, Bob, Clare, David, Elieen

## Status

- **Alice** — auth token refresh fixed and rate limiting on the transcript API
  is in; websocket reconnect repair is next.
- **Bob** — diarization drift fixed; the STT v2 endpoint published last night,
  Clare is unblocked; profiling STT latency under load today.
- **Clare** — renderer rewrite landed; integrating the STT v2 streaming
  endpoint this morning, then the caption settings work.
- **David** — tenant sharding done; retention job memory leak today, then the
  transcript search API.
- **Elieen** — reading view redesign done; speccing the caption settings panel.

## Discussion

- Bob: I couldn't let the translation idea go — I hacked a proof of concept
  last night on my own time. Translated captions render with ~2s extra latency.
  It's rough, but it works. I still think we should productize it this week.
- Alice: a prototype doesn't change the math — the board is still zero-slack
  and quantizing the model for CPU inference is on your plate today. If
  translation work bleeds into board hours we slip GA.
- Clare: for what it's worth the frontend cost is small once captions are
  stable — but not this week.
- Still unresolved; parking it for whoever owns priorities.

## Open questions

- **Live caption translation: still no decision. Needs the PM to prioritize —
  Bob's prototype exists, Alice holds the line on GA focus.**

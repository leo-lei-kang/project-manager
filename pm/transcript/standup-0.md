# Daily standup — Monday 11:00

Attendees: Alice, Bob, Clare, David, Elieen

## Status

- **Alice** — finished the GA release checklist audit this morning; starting the
  auth token refresh fix in the session API after standup.
- **Bob** — STT word-error benchmark done; picking up the diarization drift fix
  on long calls next.
- **Clare** — caption flicker on resize is fixed; starting the caption renderer
  rewrite for virtual scroll (big one, rest of today).
- **David** — transcript table indexes landed; starting tenant sharding of
  transcript storage.
- **Elieen** — caption contrast audit done; starting the transcript reading
  view redesign.

## Discussion

- Bob: an enterprise prospect asked sales on Friday for **live translation of
  captions** (real-time translated subtitles). There is no ticket for it — it's
  not on the GA board at all. I've thought about the pipeline; I could take it
  this week, translation slots in after the STT stage.
- Alice: the GA board already tiles everyone's week exactly — every launch
  blocker has to ship by Friday 17:00 and there is zero slack. Starting
  off-board translation work now puts the GA date at risk. It should wait, or
  someone above us decides it displaces something.
- Bob: understood, but the prospect is sizeable and the window may close.
- No resolution — the team can't settle scope vs. schedule on its own.

## Open questions

- **Live caption translation (not on the board): build it this week or defer?
  Needs the PM to prioritize.** If it's in, what does it displace? If it's out,
  who tells sales and when?

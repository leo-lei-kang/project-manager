# Meeting Transcripts v1

Add live transcripts to the meeting app: every meeting a customer runs gets an
accurate, searchable transcript, generated live while the meeting happens and
available the moment it ends.

Scope for this week's push: the speech-to-text pipeline with speaker labels and
live captions, the storage, retention, and export path, the in-app viewer, a
search experience end to end, and the design and accessibility pass that holds
it together. Ship behind a feature flag; the demo target is a full meeting
transcribed end-to-end.

## Task breakdown

| Task id | Title | DRI | Status | Estimate (min) |
|---------|-------|-----|--------|----------------|
| NOTES-1 | Transcribe meeting audio live (STT pipeline) | bob | todo | 420 |
| NOTES-2 | Store transcripts per meeting with retention | david | todo | 420 |
| NOTES-3 | Serve transcripts through the meeting API | alice | todo | 420 |
| NOTES-4 | In-app transcript viewer with speaker labels | clare | todo | 420 |
| NOTES-5 | Search across a meeting's transcript | david | todo | 420 |
| NOTES-6 | Design pass: transcript reading and search UX | elieen | todo | 360 |
| NOTES-7 | Speaker diarization: who said what | bob | todo | 480 |
| NOTES-8 | Streaming punctuation and casing pass | bob | todo | 480 |
| NOTES-9 | Accuracy eval harness on recorded meetings | bob | todo | 420 |
| NOTES-10 | Latency tuning for live captions | bob | todo | 420 |
| NOTES-11 | Retention sweep job and delete API | david | todo | 480 |
| NOTES-12 | Transcript export endpoint (txt/PDF) | david | todo | 480 |
| NOTES-13 | Search indexing pipeline for transcripts | david | todo | 420 |
| NOTES-14 | Access control on transcript endpoints | alice | todo | 480 |
| NOTES-15 | Live transcript push over websocket | alice | todo | 480 |
| NOTES-16 | Pagination and chunked fetch for long meetings | alice | todo | 420 |
| NOTES-17 | Feature flag and rollout wiring | alice | todo | 420 |
| NOTES-18 | Live captions view during the meeting | clare | todo | 480 |
| NOTES-19 | Search results UI with jump-to-moment | clare | todo | 480 |
| NOTES-20 | Highlight, copy, and share transcript snippets | clare | todo | 420 |
| NOTES-21 | Loading, empty, and error states in the viewer | clare | todo | 420 |
| NOTES-22 | Design: live captions and viewer polish | elieen | todo | 480 |
| NOTES-23 | Design: search flows end-to-end | elieen | todo | 480 |
| NOTES-24 | Design: mobile and responsive layouts | elieen | todo | 480 |
| NOTES-25 | Accessibility review of transcript surfaces | elieen | todo | 420 |

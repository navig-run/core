- **The goals card no longer parses every ROADMAP.md on the event loop, and a failed
  scan is a JSON 500 the UI can show.** `/apps/goals` walked every space and parsed every
  roadmap inline on the poll that asks for it, so one slow file stalled the whole gateway,
  and an unreadable spaces dir answered aiohttp's bare error page. The walk runs in a worker
  thread now, each roadmap is re-parsed only when its (mtime, size) changes, and a tapped
  milestone drops its cached parse so the next poll is never the pre-tap list.

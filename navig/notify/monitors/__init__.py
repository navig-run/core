"""Local device-sensor monitors that feed the Privacy notification category.

Each monitor is an opt-in background producer: it watches a local signal (webcam,
later mic/screen/USB) and calls ``notify.dispatch`` on a transition, so the event
lands in the deck + every channel enabled for its type in Settings → Notifications.
"""

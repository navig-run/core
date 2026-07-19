# telemetry_auditor

Privacy audit: captures all outbound/inbound network connections from VS Code and Windows telemetry services, traces each endpoint to its owner (WHOIS/ASN/PTR/GeoIP), maps data categories, locates local staging files, and generates a classified risk report. Read-only — makes no network changes.

## Structure

- `schema.json`: Interface
- `tool.py`: CLI
- worker.py: Background

# Prometheus Template for NAVIG

## Overview

Prometheus is an open-source systems monitoring and alerting toolkit. It collects and stores metrics as time series data, supporting powerful PromQL queries and alerting based on collected metrics.

## Features

- **Time Series Database**: Efficient storage of metrics data
- **PromQL**: Powerful query language for metrics analysis
- **Service Discovery**: Automatic target discovery
- **Alerting**: Alert rules with Alertmanager integration
- **Scraping**: Pull-based metrics collection
- **Federation**: Scale across multiple Prometheus servers

## Usage

### Enable the Template

```bash
navig server-template init prometheus --server <server-name>
navig server-template enable prometheus --server <server-name>
```

### Common Operations

#### Service Management

```bash
# Start Prometheus
navig run "systemctl start prometheus"

# Check status
navig run "systemctl status prometheus"

# View logs
navig run "journalctl -u prometheus -f"

# Reload configuration (without restart)
navig run "curl -X POST http://localhost:9090/-/reload"
```

#### Configuration Validation

```bash
# Check config syntax
navig run "promtool check config /etc/prometheus/prometheus.yml"

# Check alerting rules
navig run "promtool check rules /etc/prometheus/rules/*.yml"
```

#### Querying

```bash
# Check all targets
navig run "curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | {job, instance, health}'"

# Execute PromQL query
navig run 'curl -s "http://localhost:9090/api/v1/query?query=up" | jq'

# Query range
navig run 'curl -s "http://localhost:9090/api/v1/query_range?query=rate(node_cpu_seconds_total[5m])&start=2023-01-01T00:00:00Z&end=2023-01-01T01:00:00Z&step=60" | jq'

# Get all labels for a metric
navig run 'curl -s "http://localhost:9090/api/v1/labels?match[]=node_cpu_seconds_total" | jq'
```

## Configuration

### Main Configuration

Create `/etc/prometheus/prometheus.yml`:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  external_labels:
    monitor: 'my-monitor'

alerting:
  alertmanagers:
    - static_configs:
        - targets:
          - localhost:9093

rule_files:
  - /etc/prometheus/rules/*.yml

scrape_configs:
  # Prometheus itself
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']

  # Node Exporter
  - job_name: 'node'
    static_configs:
      - targets: ['localhost:9100']

  # Custom application
  - job_name: 'myapp'
    static_configs:
      - targets: ['localhost:8080']
    metrics_path: /metrics
    scrape_interval: 30s
```

### Alert Rules

Create `/etc/prometheus/rules/alerts.yml`:

```yaml
groups:
  - name: system-alerts
    rules:
      - alert: InstanceDown
        expr: up == 0
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Instance {{ $labels.instance }} down"
          description: "{{ $labels.instance }} has been down for more than 5 minutes."

      - alert: HighCPU
        expr: 100 - (avg by(instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100) > 80
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "High CPU usage on {{ $labels.instance }}"
          description: "CPU usage is {{ $value }}%"

      - alert: HighMemory
        expr: (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100 > 90
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High memory usage on {{ $labels.instance }}"
```

### Recording Rules

Create `/etc/prometheus/rules/recording.yml`:

```yaml
groups:
  - name: cpu-recording
    rules:
      - record: job:cpu_usage:avg
        expr: 100 - (avg by(job) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)

      - record: job:memory_usage:percent
        expr: (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100
```

### Service Discovery (Docker)

```yaml
scrape_configs:
  - job_name: 'docker'
    docker_sd_configs:
      - host: unix:///var/run/docker.sock
        refresh_interval: 30s
    relabel_configs:
      - source_labels: [__meta_docker_container_label_prometheus_job]
        target_label: job
      - source_labels: [__meta_docker_container_name]
        target_label: instance
```

## Default Paths

| Path | Location | Description |
|------|----------|-------------|
| config_file | `/etc/prometheus/prometheus.yml` | Main configuration |
| data_dir | `/var/lib/prometheus` | Time series data |
| rules_dir | `/etc/prometheus/rules` | Alert and recording rules |

## Default Port

- **Prometheus Web UI/API**: 9090

## Common Exporters

| Exporter | Port | Purpose |
|----------|------|---------|
| node_exporter | 9100 | System metrics |
| blackbox_exporter | 9115 | HTTP/TCP probes |
| mysqld_exporter | 9104 | MySQL metrics |
| postgres_exporter | 9187 | PostgreSQL metrics |
| nginx_exporter | 9113 | Nginx metrics |
| redis_exporter | 9121 | Redis metrics |

### Install Node Exporter

```bash
# Install
navig run "apt install prometheus-node-exporter -y"

# Start
navig run "systemctl enable prometheus-node-exporter && systemctl start prometheus-node-exporter"

# Verify
navig run "curl -s http://localhost:9100/metrics | head -20"
```

## Backup & Restore

### Create Snapshot

```bash
# Create snapshot via API
navig run "curl -XPOST http://localhost:9090/api/v1/admin/tsdb/snapshot"

# Find snapshot
navig run "ls /var/lib/prometheus/snapshots/"

# Download snapshot
navig download /var/lib/prometheus/snapshots/SNAPSHOT_NAME ./backups/
```

### Backup Configuration

```bash
# Backup all configs
navig run "tar -czvf /backup/prometheus-config.tar.gz /etc/prometheus"

# Download
navig download /backup/prometheus-config.tar.gz ./backups/
```

## Troubleshooting

### Target Down

```bash
# Check target status
navig run "curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | select(.health != \"up\")'"

# Test target manually
navig run "curl -v http://target:9100/metrics"

# Check firewall
navig run "ss -tlnp | grep 9100"
```

### High Cardinality

```bash
# Check TSDB stats
navig run "curl -s http://localhost:9090/api/v1/status/tsdb | jq"

# Find high cardinality metrics
navig run 'curl -s "http://localhost:9090/api/v1/query?query=topk(10,count by (__name__)({__name__=~\".+\"}))" | jq'
```

### Configuration Errors

```bash
# Validate configuration
navig run "promtool check config /etc/prometheus/prometheus.yml"

# Check logs for errors
navig run "journalctl -u prometheus | grep -i error | tail -20"
```

### Storage Issues

```bash
# Check disk usage
navig run "du -sh /var/lib/prometheus"

# Check TSDB status
navig run "curl -s http://localhost:9090/api/v1/status/tsdb | jq '.data.seriesCountByMetricName | to_entries | sort_by(-.value) | .[0:10]'"

# Force compaction
navig run "curl -XPOST http://localhost:9090/api/v1/admin/tsdb/compact"
```

## Security Best Practices

1. **Authentication**: Use reverse proxy with authentication
2. **TLS**: Enable HTTPS via reverse proxy
3. **Network**: Bind to localhost, use firewall rules
4. **RBAC**: Use Prometheus with --web.enable-admin-api cautiously
5. **Secrets**: Use file-based secrets for remote_write passwords

## PromQL Examples

```promql
# CPU usage percentage
100 - (avg by(instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)

# Memory usage
(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100

# Disk usage
100 - ((node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}) * 100)

# Network traffic rate
rate(node_network_receive_bytes_total[5m])

# HTTP request rate
rate(http_requests_total[5m])

# 95th percentile latency
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))
```

## References

- Official Website: https://prometheus.io
- Documentation: https://prometheus.io/docs/
- PromQL Reference: https://prometheus.io/docs/prometheus/latest/querying/basics/
- Exporters: https://prometheus.io/docs/instrumenting/exporters/
- GitHub: https://github.com/prometheus/prometheus



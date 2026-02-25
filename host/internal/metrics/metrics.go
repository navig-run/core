// Package metrics provides lightweight in-memory atomic counters for
// operational observability. No external dependencies; safe for concurrent use.
package metrics

import (
	"sync/atomic"
	"time"
)

// Counters holds all tracked metrics.
type Counters struct {
	RequestsTotal   atomic.Int64 // incremented per HTTP request (all endpoints)
	ErrorsTotal     atomic.Int64 // incremented per error response
	PythonRestarts  atomic.Int64 // incremented on each Python subprocess restart
	startTime       time.Time
}

// New returns a zero-initialised Counters with the start clock set to now.
func New() *Counters {
	return &Counters{startTime: time.Now()}
}

// IncRequests increments the requests_total counter for the given endpoint.
func (c *Counters) IncRequests()        { c.RequestsTotal.Add(1) }

// IncErrors increments the errors_total counter.
func (c *Counters) IncErrors()          { c.ErrorsTotal.Add(1) }

// IncPythonRestarts increments the python_restarts_total counter.
func (c *Counters) IncPythonRestarts()  { c.PythonRestarts.Add(1) }

// UptimeSeconds returns seconds elapsed since the host started.
func (c *Counters) UptimeSeconds() int64 {
	return int64(time.Since(c.startTime).Seconds())
}

// Snapshot returns a point-in-time copy of all metrics as a plain map,
// suitable for JSON serialisation.
func (c *Counters) Snapshot() map[string]int64 {
	return map[string]int64{
		"requests_total":        c.RequestsTotal.Load(),
		"errors_total":          c.ErrorsTotal.Load(),
		"python_restarts_total": c.PythonRestarts.Load(),
		"uptime_seconds":        c.UptimeSeconds(),
	}
}

// EndpointCounters tracks per-endpoint request counts.
type EndpointCounters struct {
	m map[string]*atomic.Int64
}

// NewEndpointCounters initialises counters for the known v1 endpoints.
func NewEndpointCounters() *EndpointCounters {
	ec := &EndpointCounters{m: make(map[string]*atomic.Int64)}
	for _, ep := range []string{
		"/v1/status",
		"/v1/inbox/ingest",
		"/v1/router/complete",
		"/v1/tools/execute",
	} {
		c := new(atomic.Int64)
		ec.m[ep] = c
	}
	return ec
}

// Inc increments the counter for path (no-op for unknown paths).
func (ec *EndpointCounters) Inc(path string) {
	if c, ok := ec.m[path]; ok {
		c.Add(1)
	}
}

// Snapshot returns a map of endpoint → count.
func (ec *EndpointCounters) Snapshot() map[string]int64 {
	out := make(map[string]int64, len(ec.m))
	for k, v := range ec.m {
		out[k] = v.Load()
	}
	return out
}

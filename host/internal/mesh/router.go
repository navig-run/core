// Package mesh provides a high-performance peer router for the NAVIG mesh
// network.  It maintains a registry of NodeRecords (mirroring the Python
// dataclass in navig/mesh/registry.py) and routes requests to the best
// available peer by composite score, with circuit-breaker and health-sweep.
//
// Design choices:
//   - Struct fields match the Python NodeRecord exactly (gateway_url: str,
//     NOT Endpoints []string).
//   - The router is safe for concurrent use from multiple goroutines.
//   - HealthSweep runs as a background goroutine with configurable interval.
//   - All HTTP calls use context.Context with configurable timeout.
package mesh

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"sort"
	"sync"
	"time"

	"go.uber.org/zap"
)

// ─────────────────────────── Constants ───────────────────────────────────────

const (
	// DegradedAfterS marks a peer degraded after this many seconds without heartbeat.
	DegradedAfterS = 45
	// OfflineAfterS marks a peer offline after this many seconds.
	OfflineAfterS = 120
	// EvictAfterS removes a peer entirely after this many seconds.
	EvictAfterS = 900
	// CircuitOpenAfterFailures opens the circuit-breaker after N consecutive failures.
	CircuitOpenAfterFailures = 3

	// ProxyTimeoutS is the default per-request timeout for forwarding.
	ProxyTimeoutS = 10
	// MaxFallbackPeers is the max number of peers to try in RouteWithFallback.
	MaxFallbackPeers = 3
)

// ─────────────────────────── NodeRecord ──────────────────────────────────────

// Health represents a peer's reachability state.
type Health string

const (
	HealthOnline   Health = "online"
	HealthDegraded Health = "degraded"
	HealthOffline  Health = "offline"
)

// NodeRecord mirrors navig.mesh.registry.NodeRecord (Python).
type NodeRecord struct {
	NodeID       string   `json:"node_id"`
	Hostname     string   `json:"hostname"`
	OS           string   `json:"os"`
	GatewayURL   string   `json:"gateway_url"` // single URL, NOT []string
	Capabilities []string `json:"capabilities"`
	Formation    string   `json:"formation"`
	Load         float64  `json:"load"`    // 0.0–1.0
	Version      string   `json:"version"`
	LastSeen     float64  `json:"last_seen"` // Unix epoch seconds
	IsSelf       bool     `json:"is_self"`

	// Circuit-breaker state (not persisted)
	consecutiveFailures int
	lastRTTms           float64
	totalProbes         int
	totalProbeFailures  int
}

// GetHealth returns the peer's current health based on last_seen age.
func (n *NodeRecord) GetHealth() Health {
	age := float64(time.Now().Unix()) - n.LastSeen
	if age < DegradedAfterS {
		return HealthOnline
	}
	if age < OfflineAfterS {
		return HealthDegraded
	}
	return HealthOffline
}

// CircuitOpen returns true when the peer should be deprioritised for routing.
func (n *NodeRecord) CircuitOpen() bool {
	return n.consecutiveFailures >= CircuitOpenAfterFailures
}

// CompositeScore returns a routing score (lower = better).
// Matches the Python formula: load*0.5 + rtt_norm*0.3 + health_pen*0.2.
// Circuit-open peers get +10 to push them to the bottom.
func (n *NodeRecord) CompositeScore() float64 {
	rttNorm := math.Min(n.lastRTTms, 500.0) / 500.0
	var healthPen float64
	switch n.GetHealth() {
	case HealthOnline:
		healthPen = 0.0
	case HealthDegraded:
		healthPen = 0.5
	default:
		healthPen = 1.0
	}
	score := n.Load*0.5 + rttNorm*0.3 + healthPen*0.2
	if n.CircuitOpen() {
		score += 10.0
	}
	return score
}

// ─────────────────────────── RoutingMetrics ──────────────────────────────────

// RoutingMetrics tracks per-peer routing statistics.
type RoutingMetrics struct {
	SuccessCount int     `json:"success_count"`
	FailureCount int     `json:"failure_count"`
	RTTSumMs     float64 `json:"-"`
	AvgRTTms     float64 `json:"avg_rtt_ms"`
	SuccessRate  float64 `json:"success_rate"`
}

func (m *RoutingMetrics) recordSuccess(rttMs float64) {
	m.SuccessCount++
	m.RTTSumMs += rttMs
	if m.SuccessCount > 0 {
		m.AvgRTTms = m.RTTSumMs / float64(m.SuccessCount)
	}
	total := m.SuccessCount + m.FailureCount
	if total > 0 {
		m.SuccessRate = float64(m.SuccessCount) / float64(total)
	}
}

func (m *RoutingMetrics) recordFailure() {
	m.FailureCount++
	total := m.SuccessCount + m.FailureCount
	if total > 0 {
		m.SuccessRate = float64(m.SuccessCount) / float64(total)
	}
}

// ─────────────────────────── FanoutResult ────────────────────────────────────

// RouteResult captures the outcome of a single routing attempt.
type RouteResult struct {
	PeerID     string        `json:"peer_id"`
	StatusCode int           `json:"status_code"`
	Body       []byte        `json:"body,omitempty"`
	RTT        time.Duration `json:"rtt_ms"`
	Err        error         `json:"error,omitempty"`
}

// ─────────────────────────── Router ──────────────────────────────────────────

// Router manages mesh peer selection and request forwarding.
type Router struct {
	mu      sync.RWMutex
	peers   map[string]*NodeRecord // node_id → record
	metrics map[string]*RoutingMetrics
	client  *http.Client
	logger  *zap.Logger
	selfID  string

	stopOnce sync.Once
	stopCh   chan struct{}
}

// NewRouter constructs a Router. selfID is this node's stable ID.
func NewRouter(selfID string, logger *zap.Logger) *Router {
	return &Router{
		peers:   make(map[string]*NodeRecord),
		metrics: make(map[string]*RoutingMetrics),
		client: &http.Client{
			Timeout: ProxyTimeoutS * time.Second,
		},
		logger: logger,
		selfID: selfID,
		stopCh: make(chan struct{}),
	}
}

// ─────────────────────────── Registration ────────────────────────────────────

// Register adds or updates a peer in the registry.
func (r *Router) Register(rec *NodeRecord) {
	r.mu.Lock()
	defer r.mu.Unlock()

	rec.LastSeen = float64(time.Now().Unix())
	r.peers[rec.NodeID] = rec

	if _, ok := r.metrics[rec.NodeID]; !ok {
		r.metrics[rec.NodeID] = &RoutingMetrics{}
	}

	r.logger.Debug("mesh: peer registered",
		zap.String("node_id", rec.NodeID),
		zap.String("gateway_url", rec.GatewayURL),
		zap.Float64("load", rec.Load),
	)
}

// Deregister removes a peer from the registry.
func (r *Router) Deregister(nodeID string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	delete(r.peers, nodeID)
	delete(r.metrics, nodeID)
	r.logger.Info("mesh: peer deregistered", zap.String("node_id", nodeID))
}

// ─────────────────────────── Routing ─────────────────────────────────────────

// sortedPeers returns remote peers sorted by CompositeScore (ascending).
// Caller must hold at least r.mu.RLock.
func (r *Router) sortedPeers(capability string) []*NodeRecord {
	candidates := make([]*NodeRecord, 0, len(r.peers))
	for _, p := range r.peers {
		if p.IsSelf || p.NodeID == r.selfID {
			continue
		}
		if p.GetHealth() == HealthOffline {
			continue
		}
		if capability != "" {
			found := false
			for _, c := range p.Capabilities {
				if c == capability {
					found = true
					break
				}
			}
			if !found {
				continue
			}
		}
		candidates = append(candidates, p)
	}
	sort.Slice(candidates, func(i, j int) bool {
		return candidates[i].CompositeScore() < candidates[j].CompositeScore()
	})
	return candidates
}

// Route forwards a JSON payload to the best peer. Returns the RouteResult.
// If no suitable peer is available, returns nil (caller should fall back to local).
func (r *Router) Route(ctx context.Context, payload []byte, capability string) *RouteResult {
	r.mu.RLock()
	peers := r.sortedPeers(capability)
	r.mu.RUnlock()

	if len(peers) == 0 {
		return nil
	}
	return r.forwardToPeer(ctx, peers[0], payload)
}

// RouteWithFallback tries up to MaxFallbackPeers in score order.
// Returns the first successful result, or the last error result.
func (r *Router) RouteWithFallback(ctx context.Context, payload []byte, capability string) *RouteResult {
	r.mu.RLock()
	peers := r.sortedPeers(capability)
	r.mu.RUnlock()

	limit := MaxFallbackPeers
	if len(peers) < limit {
		limit = len(peers)
	}

	var lastResult *RouteResult
	for i := 0; i < limit; i++ {
		result := r.forwardToPeer(ctx, peers[i], payload)
		if result.Err == nil && result.StatusCode >= 200 && result.StatusCode < 300 {
			return result
		}
		lastResult = result
		r.logger.Warn("mesh: peer failed, trying next",
			zap.String("peer", peers[i].NodeID),
			zap.Int("attempt", i+1),
			zap.Error(result.Err),
		)
	}
	return lastResult
}

// RouteParallelBest races the top-2 peers simultaneously.
// Returns whichever responds first with a 2xx.
func (r *Router) RouteParallelBest(ctx context.Context, payload []byte, capability string) *RouteResult {
	r.mu.RLock()
	peers := r.sortedPeers(capability)
	r.mu.RUnlock()

	if len(peers) == 0 {
		return nil
	}
	if len(peers) == 1 {
		return r.forwardToPeer(ctx, peers[0], payload)
	}

	// Race top 2
	type indexedResult struct {
		result *RouteResult
		idx    int
	}
	ch := make(chan indexedResult, 2)
	raceCtx, cancel := context.WithCancel(ctx)
	defer cancel()

	for i := 0; i < 2; i++ {
		go func(idx int) {
			res := r.forwardToPeer(raceCtx, peers[idx], payload)
			ch <- indexedResult{result: res, idx: idx}
		}(i)
	}

	// Return first success, or last failure
	var first, second *RouteResult
	r1 := <-ch
	if r1.result.Err == nil && r1.result.StatusCode >= 200 && r1.result.StatusCode < 300 {
		cancel() // stop the other
		return r1.result
	}
	first = r1.result

	r2 := <-ch
	if r2.result.Err == nil && r2.result.StatusCode >= 200 && r2.result.StatusCode < 300 {
		return r2.result
	}
	second = r2.result

	// Both failed — return the one with the lower RTT
	if first.RTT < second.RTT {
		return first
	}
	return second
}

// forwardToPeer executes a single HTTP POST to the peer's gateway.
func (r *Router) forwardToPeer(ctx context.Context, peer *NodeRecord, payload []byte) *RouteResult {
	url := peer.GatewayURL + "/llm/chat"
	start := time.Now()

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, nil)
	if err != nil {
		r.recordFailure(peer)
		return &RouteResult{PeerID: peer.NodeID, Err: fmt.Errorf("build request: %w", err)}
	}
	req.Header.Set("Content-Type", "application/json")
	req.Body = io.NopCloser(bytes.NewReader(payload))
	req.ContentLength = int64(len(payload))

	resp, err := r.client.Do(req)
	rtt := time.Since(start)

	if err != nil {
		r.recordFailure(peer)
		return &RouteResult{
			PeerID: peer.NodeID,
			RTT:    rtt,
			Err:    fmt.Errorf("http do: %w", err),
		}
	}
	defer resp.Body.Close()

	body, _ := readLimited(resp.Body, 1<<20) // 1 MiB max
	r.recordSuccess(peer, rtt)

	return &RouteResult{
		PeerID:     peer.NodeID,
		StatusCode: resp.StatusCode,
		Body:       body,
		RTT:        rtt,
	}
}

// ─────────────────────────── Health Sweep ────────────────────────────────────

// StartHealthSweep launches a background goroutine that evicts stale peers.
func (r *Router) StartHealthSweep(interval time.Duration) {
	go func() {
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				r.sweep()
			case <-r.stopCh:
				return
			}
		}
	}()
}

// Stop terminates the health sweep goroutine.
func (r *Router) Stop() {
	r.stopOnce.Do(func() { close(r.stopCh) })
}

func (r *Router) sweep() {
	r.mu.Lock()
	defer r.mu.Unlock()

	now := float64(time.Now().Unix())
	for id, peer := range r.peers {
		age := now - peer.LastSeen
		if age > EvictAfterS {
			delete(r.peers, id)
			delete(r.metrics, id)
			r.logger.Info("mesh: evicted stale peer",
				zap.String("node_id", id),
				zap.Float64("age_s", age),
			)
		}
	}
}

// ─────────────────────────── Observability ───────────────────────────────────

// GetMetrics returns a snapshot of routing metrics for all peers.
func (r *Router) GetMetrics() map[string]*RoutingMetrics {
	r.mu.RLock()
	defer r.mu.RUnlock()

	out := make(map[string]*RoutingMetrics, len(r.metrics))
	for k, v := range r.metrics {
		cp := *v
		out[k] = &cp
	}
	return out
}

// GetTopology returns a JSON-serialisable snapshot of all peers.
func (r *Router) GetTopology() []json.RawMessage {
	r.mu.RLock()
	defer r.mu.RUnlock()

	out := make([]json.RawMessage, 0, len(r.peers))
	for _, p := range r.peers {
		data, err := json.Marshal(p)
		if err != nil {
			continue
		}
		out = append(out, data)
	}
	return out
}

// PeerCount returns the number of registered (non-self) peers.
func (r *Router) PeerCount() int {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return len(r.peers)
}

// ─────────────────────────── Internal helpers ────────────────────────────────

func (r *Router) recordSuccess(peer *NodeRecord, rtt time.Duration) {
	peer.consecutiveFailures = 0
	peer.lastRTTms = float64(rtt.Milliseconds())
	peer.totalProbes++

	r.mu.Lock()
	if m, ok := r.metrics[peer.NodeID]; ok {
		m.recordSuccess(peer.lastRTTms)
	}
	r.mu.Unlock()

	r.logger.Debug("mesh: route success",
		zap.String("peer", peer.NodeID),
		zap.Duration("rtt", rtt),
	)
}

func (r *Router) recordFailure(peer *NodeRecord) {
	peer.consecutiveFailures++
	peer.totalProbes++
	peer.totalProbeFailures++

	r.mu.Lock()
	if m, ok := r.metrics[peer.NodeID]; ok {
		m.recordFailure()
	}
	r.mu.Unlock()

	r.logger.Warn("mesh: route failure",
		zap.String("peer", peer.NodeID),
		zap.Int("consecutive", peer.consecutiveFailures),
	)
}

// readLimited reads up to limit bytes from r.
func readLimited(r io.Reader, limit int) ([]byte, error) {
	buf := make([]byte, 0, 4096)
	tmp := make([]byte, 4096)
	for {
		n, err := r.Read(tmp)
		if n > 0 {
			buf = append(buf, tmp[:n]...)
			if len(buf) > limit {
				return buf[:limit], nil
			}
		}
		if err != nil {
			return buf, nil
		}
	}
}

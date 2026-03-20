package mesh

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"go.uber.org/zap"
	"go.uber.org/zap/zaptest"
)

func testLogger(t *testing.T) *zap.Logger {
	return zaptest.NewLogger(t)
}

func newTestPeer(id, url string, load float64) *NodeRecord {
	return &NodeRecord{
		NodeID:       id,
		Hostname:     "test-" + id,
		OS:           "linux",
		GatewayURL:   url,
		Capabilities: []string{"llm", "shell"},
		Formation:    "test",
		Load:         load,
		Version:      "2.4.14",
		LastSeen:     float64(time.Now().Unix()),
	}
}

// ─────────────────────────── NodeRecord tests ────────────────────────────────

func TestNodeRecord_Health(t *testing.T) {
	now := float64(time.Now().Unix())

	tests := []struct {
		name     string
		lastSeen float64
		want     Health
	}{
		{"online", now - 10, HealthOnline},
		{"degraded", now - float64(DegradedAfterS+5), HealthDegraded},
		{"offline", now - float64(OfflineAfterS+5), HealthOffline},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			n := &NodeRecord{LastSeen: tc.lastSeen}
			if got := n.GetHealth(); got != tc.want {
				t.Errorf("GetHealth() = %v, want %v", got, tc.want)
			}
		})
	}
}

func TestNodeRecord_CircuitOpen(t *testing.T) {
	n := &NodeRecord{}
	if n.CircuitOpen() {
		t.Error("should not be open with 0 failures")
	}
	n.consecutiveFailures = CircuitOpenAfterFailures
	if !n.CircuitOpen() {
		t.Error("should be open after N failures")
	}
}

func TestNodeRecord_CompositeScore_CircuitPenalty(t *testing.T) {
	n := &NodeRecord{Load: 0.5, LastSeen: float64(time.Now().Unix())}
	base := n.CompositeScore()

	n.consecutiveFailures = CircuitOpenAfterFailures
	penalized := n.CompositeScore()

	if penalized-base < 9.5 {
		t.Errorf("circuit-open penalty too small: base=%.2f, penalized=%.2f", base, penalized)
	}
}

// ─────────────────────────── Router registration tests ───────────────────────

func TestRouter_RegisterAndCount(t *testing.T) {
	r := NewRouter("self-1", testLogger(t))

	r.Register(newTestPeer("peer-1", "http://1.2.3.4:8789", 0.3))
	r.Register(newTestPeer("peer-2", "http://5.6.7.8:8789", 0.7))

	if got := r.PeerCount(); got != 2 {
		t.Errorf("PeerCount() = %d, want 2", got)
	}
}

func TestRouter_Deregister(t *testing.T) {
	r := NewRouter("self-1", testLogger(t))
	r.Register(newTestPeer("peer-1", "http://1.2.3.4:8789", 0.3))
	r.Deregister("peer-1")

	if got := r.PeerCount(); got != 0 {
		t.Errorf("PeerCount() after deregister = %d, want 0", got)
	}
}

func TestRouter_RegisterUpdatesLastSeen(t *testing.T) {
	r := NewRouter("self-1", testLogger(t))
	peer := newTestPeer("peer-1", "http://1.2.3.4:8789", 0.3)
	peer.LastSeen = 100 // old timestamp
	r.Register(peer)

	r.mu.RLock()
	stored := r.peers["peer-1"]
	r.mu.RUnlock()

	if stored.LastSeen < float64(time.Now().Unix()-5) {
		t.Error("Register should update LastSeen to now")
	}
}

// ─────────────────────────── Routing tests ───────────────────────────────────

func TestRouter_Route_Success(t *testing.T) {
	var hits int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&hits, 1)
		w.WriteHeader(http.StatusOK)
		fmt.Fprint(w, `{"status":"ok"}`)
	}))
	defer srv.Close()

	r := NewRouter("self-1", testLogger(t))
	r.Register(newTestPeer("peer-1", srv.URL, 0.2))

	result := r.Route(context.Background(), []byte(`{"prompt":"hello"}`), "")
	if result == nil {
		t.Fatal("Route returned nil")
	}
	if result.Err != nil {
		t.Fatalf("Route error: %v", result.Err)
	}
	if result.StatusCode != 200 {
		t.Errorf("status = %d, want 200", result.StatusCode)
	}
	if atomic.LoadInt32(&hits) != 1 {
		t.Errorf("expected 1 hit, got %d", atomic.LoadInt32(&hits))
	}
}

func TestRouter_Route_NoPeers(t *testing.T) {
	r := NewRouter("self-1", testLogger(t))
	result := r.Route(context.Background(), []byte(`{}`), "")
	if result != nil {
		t.Error("Route with no peers should return nil")
	}
}

func TestRouter_Route_SkipsSelf(t *testing.T) {
	r := NewRouter("self-1", testLogger(t))
	r.Register(newTestPeer("self-1", "http://localhost:9999", 0.1))

	result := r.Route(context.Background(), []byte(`{}`), "")
	if result != nil {
		t.Error("Route should skip self node")
	}
}

func TestRouter_Route_FiltersByCapability(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	r := NewRouter("self-1", testLogger(t))
	peer := newTestPeer("peer-1", srv.URL, 0.2)
	peer.Capabilities = []string{"shell"} // no "gpu"
	r.Register(peer)

	result := r.Route(context.Background(), []byte(`{}`), "gpu")
	if result != nil {
		t.Error("Route should return nil when no peer has required capability")
	}

	// With matching capability
	result = r.Route(context.Background(), []byte(`{}`), "shell")
	if result == nil {
		t.Error("Route should find peer with matching capability")
	}
}

func TestRouter_RouteWithFallback(t *testing.T) {
	callCount := int32(0)
	// First server fails, second succeeds
	srv1 := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&callCount, 1)
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv1.Close()

	srv2 := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&callCount, 1)
		w.WriteHeader(http.StatusOK)
		fmt.Fprint(w, `{"from":"peer-2"}`)
	}))
	defer srv2.Close()

	r := NewRouter("self-1", testLogger(t))
	r.Register(newTestPeer("peer-1", srv1.URL, 0.1)) // lower score = tried first
	r.Register(newTestPeer("peer-2", srv2.URL, 0.5))

	result := r.RouteWithFallback(context.Background(), []byte(`{}`), "")
	if result == nil {
		t.Fatal("RouteWithFallback returned nil")
	}
	if result.StatusCode != 200 {
		t.Errorf("expected 200 from fallback, got %d", result.StatusCode)
	}
	if result.PeerID != "peer-2" {
		t.Errorf("expected peer-2, got %s", result.PeerID)
	}
}

func TestRouter_RouteWithFallback_AllFail(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadGateway)
	}))
	defer srv.Close()

	r := NewRouter("self-1", testLogger(t))
	r.Register(newTestPeer("peer-1", srv.URL, 0.2))

	result := r.RouteWithFallback(context.Background(), []byte(`{}`), "")
	if result == nil {
		t.Fatal("expected non-nil result even on all-fail")
	}
	if result.StatusCode == 200 {
		t.Error("expected non-200 status")
	}
}

// ─────────────────────────── Health sweep tests ──────────────────────────────

func TestRouter_Sweep_EvictsStale(t *testing.T) {
	r := NewRouter("self-1", testLogger(t))
	peer := newTestPeer("stale-1", "http://dead:8789", 0.5)
	peer.LastSeen = float64(time.Now().Unix()) - float64(EvictAfterS+100)
	r.mu.Lock()
	r.peers[peer.NodeID] = peer
	r.metrics[peer.NodeID] = &RoutingMetrics{}
	r.mu.Unlock()

	r.sweep()

	if got := r.PeerCount(); got != 0 {
		t.Errorf("sweep should have evicted stale peer, count = %d", got)
	}
}

func TestRouter_Sweep_KeepsFresh(t *testing.T) {
	r := NewRouter("self-1", testLogger(t))
	r.Register(newTestPeer("fresh-1", "http://alive:8789", 0.3))

	r.sweep()

	if got := r.PeerCount(); got != 1 {
		t.Errorf("sweep should keep fresh peer, count = %d", got)
	}
}

// ─────────────────────────── Metrics tests ───────────────────────────────────

func TestRouter_GetMetrics(t *testing.T) {
	r := NewRouter("self-1", testLogger(t))

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	r.Register(newTestPeer("peer-1", srv.URL, 0.2))
	_ = r.Route(context.Background(), []byte(`{}`), "")

	metrics := r.GetMetrics()
	m, ok := metrics["peer-1"]
	if !ok {
		t.Fatal("missing metrics for peer-1")
	}
	if m.SuccessCount != 1 {
		t.Errorf("SuccessCount = %d, want 1", m.SuccessCount)
	}
}

func TestRouter_FailureMarksCircuit(t *testing.T) {
	// Server that immediately closes connection
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hj, ok := w.(http.Hijacker)
		if !ok {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		conn, _, _ := hj.Hijack()
		conn.Close()
	}))
	defer srv.Close()

	r := NewRouter("self-1", testLogger(t))
	r.Register(newTestPeer("peer-1", srv.URL, 0.2))

	for i := 0; i < CircuitOpenAfterFailures+1; i++ {
		_ = r.Route(context.Background(), []byte(`{}`), "")
	}

	r.mu.RLock()
	peer := r.peers["peer-1"]
	r.mu.RUnlock()

	if !peer.CircuitOpen() {
		t.Error("circuit should be open after repeated failures")
	}
}

// ─────────────────────────── Topology test ───────────────────────────────────

func TestRouter_GetTopology(t *testing.T) {
	r := NewRouter("self-1", testLogger(t))
	r.Register(newTestPeer("peer-1", "http://1.2.3.4:8789", 0.3))
	r.Register(newTestPeer("peer-2", "http://5.6.7.8:8789", 0.7))

	topo := r.GetTopology()
	if len(topo) != 2 {
		t.Errorf("topology length = %d, want 2", len(topo))
	}

	// Verify it's valid JSON
	for i, raw := range topo {
		var node map[string]interface{}
		if err := json.Unmarshal(raw, &node); err != nil {
			t.Errorf("topology[%d] invalid JSON: %v", i, err)
		}
	}
}

// ─────────────────────────── Stop test ───────────────────────────────────────

func TestRouter_Stop(t *testing.T) {
	r := NewRouter("self-1", testLogger(t))
	r.StartHealthSweep(50 * time.Millisecond)

	// Give it a tick
	time.Sleep(100 * time.Millisecond)
	r.Stop()

	// Should not panic on double stop
	r.Stop()
}

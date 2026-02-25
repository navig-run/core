// navig-core/host/internal/gateway/webhooks.go
// Package gateway provides the NAVIG webhook subsystem.
//
// Inbound webhooks: external services trigger NAVIG tasks via HTTP POST
//   POST /webhook/in/{token}  — authenticated endpoint, any JSON payload
//   Payload: {"intent": "browse", "url": "...", "steps": [...]}
//   Or generic: {"event": "github.push", "repo": "...", "branch": "main"}
//
// Outbound webhooks: NAVIG notifies external services on task events
//   Task completed, failed, CAPTCHA detected, 2FA requested — all emit
//   a POST to all registered outbound webhook URLs.
//
// Setup:
//   navig webhook add-inbound --name "GitHub Push" --secret <secret>
//   navig webhook add-outbound --url https://hooks.zapier.com/xyz --events task_complete,task_fail
//   navig webhook list
//   navig webhook disable <id>
//
// Security:
//   Inbound: HMAC-SHA256 signature in X-Navig-Signature header
//   Outbound: HMAC-SHA256 body signature in X-Navig-Signature header
//
// Storage: ~/.navig/webhooks.yaml
package gateway

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"time"

	"gopkg.in/yaml.v3"
)

// ─────────────────────────── Types ───────────────────────────────────────────

// WebhookKind distinguishes inbound from outbound webhooks.
type WebhookKind string

const (
	WebhookInbound  WebhookKind = "inbound"
	WebhookOutbound WebhookKind = "outbound"
)

// WebhookEventFilter is a list of event names an outbound webhook subscribes to.
// Empty slice = all events.
type WebhookEventFilter []string

// InboundWebhook is an authenticated HTTP endpoint that triggers NAVIG tasks.
type InboundWebhook struct {
	ID          string    `yaml:"id"          json:"id"`
	Name        string    `yaml:"name"        json:"name"`
	Token       string    `yaml:"token"       json:"token"`       // URL token (/webhook/in/{token})
	Secret      string    `yaml:"secret"      json:"secret"`      // HMAC secret (masked in output)
	Enabled     bool      `yaml:"enabled"     json:"enabled"`
	CreatedAt   time.Time `yaml:"created_at"  json:"created_at"`
	LastTrigger time.Time `yaml:"last_trigger,omitempty" json:"last_trigger,omitempty"`
	TriggerCount int      `yaml:"trigger_count" json:"trigger_count"`
}

// OutboundWebhook is a URL that NAVIG POSTs to when events occur.
type OutboundWebhook struct {
	ID      string             `yaml:"id"     json:"id"`
	Name    string             `yaml:"name"   json:"name"`
	URL     string             `yaml:"url"    json:"url"`
	Secret  string             `yaml:"secret" json:"secret"` // signs outbound payloads
	Events  WebhookEventFilter `yaml:"events" json:"events"` // empty = all
	Enabled bool               `yaml:"enabled" json:"enabled"`
	CreatedAt time.Time        `yaml:"created_at" json:"created_at"`
}

// WebhookEvent is the payload sent to outbound webhooks.
type WebhookEvent struct {
	Event     string         `json:"event"`      // "task_complete" | "task_fail" | "captcha" | "2fa" | "task_start"
	TaskID    string         `json:"task_id,omitempty"`
	Timestamp string         `json:"timestamp"`
	Data      map[string]any `json:"data,omitempty"`
}

// InboundPayload is the validated payload from an inbound webhook POST.
type InboundPayload struct {
	WebhookID string          `json:"webhook_id"`
	Raw       json.RawMessage `json:"raw"` // original body
}

// ─────────────────────────── Registry ────────────────────────────────────────

// WebhookRegistry manages all inbound and outbound webhooks.
type WebhookRegistry struct {
	mu         sync.RWMutex
	Inbound    []*InboundWebhook  `yaml:"inbound"`
	Outbound   []*OutboundWebhook `yaml:"outbound"`
	configPath string
	httpClient *http.Client
	inboundCh  chan InboundPayload // channel for incoming payloads to be processed
}

// NewWebhookRegistry loads the registry from the given YAML file.
func NewWebhookRegistry(configPath string) (*WebhookRegistry, error) {
	r := &WebhookRegistry{
		configPath: configPath,
		httpClient: &http.Client{Timeout: 15 * time.Second},
		inboundCh:  make(chan InboundPayload, 64),
	}
	if err := r.load(); err != nil && !os.IsNotExist(err) {
		return nil, fmt.Errorf("webhook registry: load: %w", err)
	}
	return r, nil
}

// InboundPayloads returns the channel of validated inbound payloads.
// The host daemon reads from this channel and routes to the task executor.
func (r *WebhookRegistry) InboundPayloads() <-chan InboundPayload {
	return r.inboundCh
}

// ─────────────────────────── Inbound management ──────────────────────────────

// AddInbound creates a new inbound webhook and returns it.
func (r *WebhookRegistry) AddInbound(name string) (*InboundWebhook, error) {
	r.mu.Lock()
	defer r.mu.Unlock()

	token, err := generateSecret(16)
	if err != nil {
		return nil, err
	}
	secret, err := generateSecret(32)
	if err != nil {
		return nil, err
	}

	wh := &InboundWebhook{
		ID:        generateID(),
		Name:      name,
		Token:     token,
		Secret:    secret,
		Enabled:   true,
		CreatedAt: time.Now(),
	}
	r.Inbound = append(r.Inbound, wh)
	return wh, r.save()
}

// HandleInbound validates an inbound HTTP request and enqueues the payload.
// Returns 200 immediately; processing is async.
func (r *WebhookRegistry) HandleInbound(w http.ResponseWriter, req *http.Request, token string) {
	r.mu.RLock()
	var wh *InboundWebhook
	for _, v := range r.Inbound {
		if v.Token == token && v.Enabled {
			wh = v
			break
		}
	}
	r.mu.RUnlock()

	if wh == nil {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}

	body, err := io.ReadAll(io.LimitReader(req.Body, 1<<20)) // 1 MB limit
	if err != nil {
		http.Error(w, "read error", http.StatusBadRequest)
		return
	}

	// Verify HMAC if secret is set
	if wh.Secret != "" {
		sig := req.Header.Get("X-Navig-Signature")
		if !verifyHMAC(body, wh.Secret, sig) {
			http.Error(w, "invalid signature", http.StatusUnauthorized)
			return
		}
	}

	// Enqueue (non-blocking; drop if full)
	payload := InboundPayload{WebhookID: wh.ID, Raw: json.RawMessage(body)}
	select {
	case r.inboundCh <- payload:
	default:
		// Queue full — log but still return 200 to caller
		fmt.Printf("[webhook] inbound queue full, dropping payload from %s\n", token)
	}

	// Update last_trigger async
	go func() {
		r.mu.Lock()
		wh.LastTrigger = time.Now()
		wh.TriggerCount++
		_ = r.save()
		r.mu.Unlock()
	}()

	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(`{"ok":true}`))
}

// ─────────────────────────── Outbound management ─────────────────────────────

// AddOutbound registers a new outbound webhook.
func (r *WebhookRegistry) AddOutbound(name, url string, events []string) (*OutboundWebhook, error) {
	r.mu.Lock()
	defer r.mu.Unlock()

	secret, err := generateSecret(32)
	if err != nil {
		return nil, err
	}

	wh := &OutboundWebhook{
		ID:        generateID(),
		Name:      name,
		URL:       url,
		Secret:    secret,
		Events:    events,
		Enabled:   true,
		CreatedAt: time.Now(),
	}
	r.Outbound = append(r.Outbound, wh)
	return wh, r.save()
}

// Emit sends an event to all matching outbound webhooks. Async, non-blocking.
func (r *WebhookRegistry) Emit(event WebhookEvent) {
	event.Timestamp = time.Now().UTC().Format(time.RFC3339)
	payload, err := json.Marshal(event)
	if err != nil {
		return
	}

	r.mu.RLock()
	targets := make([]*OutboundWebhook, 0, len(r.Outbound))
	for _, wh := range r.Outbound {
		if !wh.Enabled {
			continue
		}
		if len(wh.Events) == 0 || contains(wh.Events, event.Event) {
			targets = append(targets, wh)
		}
	}
	r.mu.RUnlock()

	for _, wh := range targets {
		go r.deliverOutbound(wh, payload)
	}
}

// deliverOutbound POSTs the payload to a single outbound webhook with retries.
func (r *WebhookRegistry) deliverOutbound(wh *OutboundWebhook, payload []byte) {
	const maxRetries = 3
	const baseDelay = 2 * time.Second

	for attempt := 0; attempt < maxRetries; attempt++ {
		if attempt > 0 {
			time.Sleep(baseDelay * time.Duration(1<<(attempt-1)))
		}

		req, err := http.NewRequestWithContext(context.Background(), "POST", wh.URL, bytes.NewReader(payload))
		if err != nil {
			fmt.Printf("[webhook] outbound request error (%s): %v\n", wh.Name, err)
			return
		}
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("User-Agent", "NAVIG-Webhook/1.0")

		// HMAC signature
		if wh.Secret != "" {
			sig := signHMAC(payload, wh.Secret)
			req.Header.Set("X-Navig-Signature", sig)
		}

		resp, err := r.httpClient.Do(req)
		if err != nil {
			fmt.Printf("[webhook] outbound delivery failed (%s, attempt %d): %v\n", wh.Name, attempt+1, err)
			continue
		}
		resp.Body.Close()

		if resp.StatusCode >= 200 && resp.StatusCode < 300 {
			fmt.Printf("[webhook] outbound delivered to %s (status=%d)\n", wh.Name, resp.StatusCode)
			return
		}
		fmt.Printf("[webhook] outbound non-2xx (%s, attempt %d): %d\n", wh.Name, attempt+1, resp.StatusCode)
	}
	fmt.Printf("[webhook] outbound permanently failed: %s\n", wh.Name)
}

// ─────────────────────────── CRUD ────────────────────────────────────────────

// DisableWebhook disables a webhook by ID (inbound or outbound).
func (r *WebhookRegistry) DisableWebhook(id string) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, wh := range r.Inbound {
		if wh.ID == id {
			wh.Enabled = false
			return r.save()
		}
	}
	for _, wh := range r.Outbound {
		if wh.ID == id {
			wh.Enabled = false
			return r.save()
		}
	}
	return fmt.Errorf("webhook not found: %s", id)
}

// DeleteWebhook removes a webhook by ID.
func (r *WebhookRegistry) DeleteWebhook(id string) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	for i, wh := range r.Inbound {
		if wh.ID == id {
			r.Inbound = append(r.Inbound[:i], r.Inbound[i+1:]...)
			return r.save()
		}
	}
	for i, wh := range r.Outbound {
		if wh.ID == id {
			r.Outbound = append(r.Outbound[:i], r.Outbound[i+1:]...)
			return r.save()
		}
	}
	return fmt.Errorf("webhook not found: %s", id)
}

// ListJSON returns all webhooks as JSON. Secrets are masked.
func (r *WebhookRegistry) ListJSON() ([]byte, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()

	type safeInbound struct {
		ID           string    `json:"id"`
		Name         string    `json:"name"`
		Token        string    `json:"token"`
		SecretMasked string    `json:"secret"`
		Enabled      bool      `json:"enabled"`
		TriggerCount int       `json:"trigger_count"`
		LastTrigger  time.Time `json:"last_trigger,omitempty"`
	}
	type safeOutbound struct {
		ID      string   `json:"id"`
		Name    string   `json:"name"`
		URL     string   `json:"url"`
		Events  []string `json:"events"`
		Enabled bool     `json:"enabled"`
	}

	result := struct {
		Inbound  []safeInbound  `json:"inbound"`
		Outbound []safeOutbound `json:"outbound"`
	}{}

	for _, wh := range r.Inbound {
		result.Inbound = append(result.Inbound, safeInbound{
			ID:           wh.ID,
			Name:         wh.Name,
			Token:        wh.Token,
			SecretMasked: maskSecret(wh.Secret),
			Enabled:      wh.Enabled,
			TriggerCount: wh.TriggerCount,
			LastTrigger:  wh.LastTrigger,
		})
	}
	for _, wh := range r.Outbound {
		result.Outbound = append(result.Outbound, safeOutbound{
			ID:      wh.ID,
			Name:    wh.Name,
			URL:     wh.URL,
			Events:  wh.Events,
			Enabled: wh.Enabled,
		})
	}

	return json.MarshalIndent(result, "", "  ")
}

// ─────────────────────────── persistence ─────────────────────────────────────

func (r *WebhookRegistry) load() error {
	data, err := os.ReadFile(r.configPath)
	if err != nil {
		return err
	}
	return yaml.Unmarshal(data, r)
}

func (r *WebhookRegistry) save() error {
	_ = os.MkdirAll(filepath.Dir(r.configPath), 0700)
	data, err := yaml.Marshal(r)
	if err != nil {
		return err
	}
	return os.WriteFile(r.configPath, data, 0600)
}

// ─────────────────────────── helpers ─────────────────────────────────────────

func generateID() string {
	b := make([]byte, 4)
	_, _ = rand.Read(b)
	return fmt.Sprintf("wh%s", hex.EncodeToString(b))
}

func generateSecret(n int) (string, error) {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}

func signHMAC(body []byte, secret string) string {
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write(body)
	return "sha256=" + hex.EncodeToString(mac.Sum(nil))
}

func verifyHMAC(body []byte, secret, sig string) bool {
	expected := signHMAC(body, secret)
	return hmac.Equal([]byte(expected), []byte(sig))
}

func maskSecret(s string) string {
	if len(s) <= 8 {
		return "****"
	}
	return s[:4] + "****" + s[len(s)-4:]
}

func contains(slice []string, s string) bool {
	for _, v := range slice {
		if v == s {
			return true
		}
	}
	return false
}

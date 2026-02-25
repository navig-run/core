// Package gateway — webhook REST API handlers
//
// Mounts on the existing Gin/chi router used by the host daemon.
// Integrate in server.go / main.go like:
//
//	reg, _ := gateway.NewWebhookRegistry(path.Join(configDir, "webhooks.yaml"))
//	gateway.RegisterWebhookRoutes(router, reg)
//	go reg.ProcessInbound(taskExecutor.Submit)  // pipe inbound → task engine
//
// Routes:
//
//	POST   /webhook/in/:token                     Authenticated inbound trigger
//	GET    /api/v1/webhooks                        List all webhooks (masked)
//	POST   /api/v1/webhooks/inbound                Create inbound webhook
//	POST   /api/v1/webhooks/outbound               Register outbound webhook
//	POST   /api/v1/webhooks/:id/disable            Disable webhook
//	DELETE /api/v1/webhooks/:id                    Delete webhook
//	POST   /api/v1/webhooks/:id/test               Send test event to outbound
package gateway

import (
	"encoding/json"
	"net/http"
	"strings"
)

// WebhookRouter wraps WebhookRegistry and exposes it as HTTP handlers.
type WebhookRouter struct {
	reg *WebhookRegistry
}

// NewWebhookRouter creates a new WebhookRouter wrapping the given registry.
func NewWebhookRouter(reg *WebhookRegistry) *WebhookRouter {
	return &WebhookRouter{reg: reg}
}

// RegisterRoutes mounts all webhook routes on an http.ServeMux (stdlib).
// If you use chi or gin, wrap the handler methods directly.
func (wr *WebhookRouter) RegisterRoutes(mux *http.ServeMux) {
	// Inbound trigger
	mux.HandleFunc("/webhook/in/", wr.handleInboundTrigger)

	// Management API
	mux.HandleFunc("/api/v1/webhooks", wr.handleWebhooks)
	mux.HandleFunc("/api/v1/webhooks/inbound", wr.handleCreateInbound)
	mux.HandleFunc("/api/v1/webhooks/outbound", wr.handleCreateOutbound)
	mux.HandleFunc("/api/v1/webhooks/", wr.handleWebhookByID)
}

// ── Handlers ──────────────────────────────────────────────────────────────────

func (wr *WebhookRouter) handleInboundTrigger(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	// Extract token from path: /webhook/in/<token>
	token := strings.TrimPrefix(r.URL.Path, "/webhook/in/")
	token = strings.TrimRight(token, "/")
	if token == "" {
		http.Error(w, "missing token", http.StatusBadRequest)
		return
	}
	wr.reg.HandleInbound(w, r, token)
}

func (wr *WebhookRouter) handleWebhooks(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	data, err := wr.reg.ListJSON()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Write(data)
}

func (wr *WebhookRouter) handleCreateInbound(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		Name string `json:"name"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.Name == "" {
		http.Error(w, "missing name", http.StatusBadRequest)
		return
	}
	wh, err := wr.reg.AddInbound(body.Name)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	jsonOK(w, map[string]any{"ok": true, "webhook": wh})
}

func (wr *WebhookRouter) handleCreateOutbound(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		Name   string   `json:"name"`
		URL    string   `json:"url"`
		Events []string `json:"events"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.URL == "" {
		http.Error(w, "missing url", http.StatusBadRequest)
		return
	}
	if body.Name == "" {
		body.Name = body.URL
	}
	wh, err := wr.reg.AddOutbound(body.Name, body.URL, body.Events)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	jsonOK(w, map[string]any{"ok": true, "webhook": wh})
}

func (wr *WebhookRouter) handleWebhookByID(w http.ResponseWriter, r *http.Request) {
	// /api/v1/webhooks/<id>[/action]
	rest := strings.TrimPrefix(r.URL.Path, "/api/v1/webhooks/")
	parts := strings.SplitN(rest, "/", 2)
	id := parts[0]
	action := ""
	if len(parts) > 1 {
		action = parts[1]
	}

	switch {
	case r.Method == http.MethodDelete && action == "":
		if err := wr.reg.DeleteWebhook(id); err != nil {
			http.Error(w, err.Error(), http.StatusNotFound)
			return
		}
		jsonOK(w, map[string]any{"ok": true})

	case r.Method == http.MethodPost && action == "disable":
		if err := wr.reg.DisableWebhook(id); err != nil {
			http.Error(w, err.Error(), http.StatusNotFound)
			return
		}
		jsonOK(w, map[string]any{"ok": true})

	case r.Method == http.MethodPost && action == "test":
		wr.reg.Emit(WebhookEvent{
			Event:  "test",
			TaskID: id,
			Data:   map[string]any{"message": "NAVIG test event"},
		})
		jsonOK(w, map[string]any{"ok": true})

	default:
		http.Error(w, "not found", http.StatusNotFound)
	}
}

func jsonOK(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}

// Package apiv1 implements the Gin-based HTTP API for NAVIG Core Host v1.
//
// Routes:
//
//	GET  /v1/status                 — public, no auth
//	POST /v1/inbox/ingest           — requires scope inbox:write
//	POST /v1/router/complete        — requires scope router:call  (proxied to Python)
//	POST /v1/tools/execute          — requires scope tools:exec
package apiv1

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"

	"navig-core/host/internal/gateway"
	"navig-core/host/internal/health"
	"navig-core/host/internal/metrics"
	"navig-core/host/internal/token"
)

// GatewayProvider is the minimal interface the API requires from the gateway manager.
// *gateway.Manager satisfies this interface.
type GatewayProvider interface {
	Status() gateway.Status
	BaseURL() string
}

const defaultMaxBodyBytes = 1 << 20 // 1 MB

// Config holds settings for the v1 API server.
type Config struct {
	Addr         string // e.g. "127.0.0.1:4747"
	MaxBodyBytes int64  // default: 1 MB
	Version      string // binary version string, embedded at build time
}

// Server is the Gin HTTP server for /v1.
type Server struct {
	cfg     Config
	tokens  *token.Store
	gw      GatewayProvider
	checker *health.Checker
	met     *metrics.Counters
	epMet   *metrics.EndpointCounters
	logger  *zap.Logger
	engine  *gin.Engine
	srv     *http.Server
	started time.Time
}

// New creates and configures (but does not start) the v1 server.
func New(
	cfg Config,
	tokens *token.Store,
	gw GatewayProvider,
	checker *health.Checker,
	met *metrics.Counters,
	logger *zap.Logger,
) *Server {
	if cfg.MaxBodyBytes == 0 {
		cfg.MaxBodyBytes = defaultMaxBodyBytes
	}

	gin.SetMode(gin.ReleaseMode)
	engine := gin.New()

	s := &Server{
		cfg:     cfg,
		tokens:  tokens,
		gw:      gw,
		checker: checker,
		met:     met,
		epMet:   metrics.NewEndpointCounters(),
		logger:  logger,
		engine:  engine,
		started: time.Now(),
	}

	s.setupRoutes()

	s.srv = &http.Server{
		Addr:              cfg.Addr,
		Handler:           engine,
		ReadHeaderTimeout: 10 * time.Second,
		WriteTimeout:      130 * time.Second, // ≥ proxy timeout
		IdleTimeout:       120 * time.Second,
	}
	return s
}

// Handler returns the underlying http.Handler (useful for httptest.NewServer).
func (s *Server) Handler() http.Handler { return s.engine }

// ListenAndServe starts the server (blocking; returns http.ErrServerClosed on clean stop).
func (s *Server) ListenAndServe() error {
	s.logger.Info("api v1 listening", zap.String("addr", s.cfg.Addr))
	return s.srv.ListenAndServe()
}

// Shutdown gracefully stops the server within 5 s.
func (s *Server) Shutdown() {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = s.srv.Shutdown(ctx)
}

// --- Route configuration -----------------------------------------------------

func (s *Server) setupRoutes() {
	r := s.engine

	// Global middleware
	r.Use(requestIDMiddleware())
	r.Use(s.requestLoggerMiddleware())
	r.Use(gin.Recovery())
	r.Use(bodySizeLimiter(s.cfg.MaxBodyBytes))

	v1 := r.Group("/v1")

	// Public
	v1.GET("/status", s.handleStatus)

	// Authenticated
	v1.POST("/inbox/ingest", s.requireScope(token.ScopeInboxWrite), s.handleInboxIngest)
	v1.POST("/router/complete", s.requireScope(token.ScopeRouterCall), s.handleRouterComplete)
	v1.POST("/tools/execute", s.requireScope(token.ScopeToolsExec), s.handleToolsExecute)
}

// --- Middleware --------------------------------------------------------------

// requestIDMiddleware injects a unique X-Request-Id into every response.
func requestIDMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		id := c.GetHeader("X-Request-Id")
		if id == "" {
			id = generateRequestID()
		}
		c.Set("request_id", id)
		c.Header("X-Request-Id", id)
		c.Next()
	}
}

func (s *Server) requestLoggerMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		start := time.Now()
		c.Next()
		s.met.IncRequests()
		s.epMet.Inc(c.FullPath())
		status := c.Writer.Status()
		if status >= 400 {
			s.met.IncErrors()
		}
		s.logger.Debug("request",
			zap.String("method", c.Request.Method),
			zap.String("path", c.Request.URL.Path),
			zap.Int("status", status),
			zap.Duration("duration", time.Since(start)),
			zap.String("request_id", c.GetString("request_id")),
		)
	}
}

// bodySizeLimiter rejects bodies larger than maxBytes with HTTP 413.
func bodySizeLimiter(maxBytes int64) gin.HandlerFunc {
	return func(c *gin.Context) {
		if c.Request.ContentLength > maxBytes {
			errResponse(c, http.StatusRequestEntityTooLarge,
				"request_too_large",
				fmt.Sprintf("request body exceeds limit of %d bytes", maxBytes))
			c.Abort()
			return
		}
		c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, maxBytes)
		c.Next()
	}
}

// requireScope returns a middleware that validates the Bearer token and its scope.
func (s *Server) requireScope(required token.Scope) gin.HandlerFunc {
	return func(c *gin.Context) {
		raw := extractBearer(c.GetHeader("Authorization"))
		entry, err := s.tokens.ValidateWithScope(raw, required)
		if err != nil {
			msg := "invalid or missing token"
			if strings.Contains(err.Error(), "missing scope") {
				msg = fmt.Sprintf("token lacks required scope %q", required)
			}
			errResponse(c, http.StatusUnauthorized, "unauthorized", msg)
			c.Abort()
			return
		}
		c.Set("token_entry", entry)
		c.Next()
	}
}

// --- Handlers ----------------------------------------------------------------

// GET /v1/status — public, no auth
func (s *Server) handleStatus(c *gin.Context) {
	report := s.checker.Latest()
	c.JSON(http.StatusOK, gin.H{
		"status":  report.Status.String(),
		"version": s.cfg.Version,
		"uptime":  s.met.UptimeSeconds(),
		"python":  s.gw.Status().String(),
		"metrics": s.met.Snapshot(),
		"checks":  report.Checks,
	})
}

// IngestRequest is the body accepted by POST /v1/inbox/ingest.
type IngestRequest struct {
	Source   string                 `json:"source" binding:"required"`
	Content  string                 `json:"content" binding:"required"`
	Metadata map[string]interface{} `json:"metadata"`
}

// POST /v1/inbox/ingest — requires inbox:write
func (s *Server) handleInboxIngest(c *gin.Context) {
	var req IngestRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		errResponse(c, http.StatusBadRequest, "invalid_request", err.Error())
		return
	}
	id := generateRequestID()
	s.logger.Info("inbox ingest",
		zap.String("source", req.Source),
		zap.String("id", id),
		zap.String("request_id", c.GetString("request_id")),
	)
	c.JSON(http.StatusOK, gin.H{"id": id, "status": "queued"})
}

// POST /v1/router/complete — requires router:call — proxied to Python
func (s *Server) handleRouterComplete(c *gin.Context) {
	if s.gw.Status() == gateway.StatusDown || s.gw.Status() == gateway.StatusStarting {
		errResponse(c, http.StatusServiceUnavailable,
			"service_unavailable",
			"Python router is not running. Host is in degraded mode.")
		return
	}

	target := s.gw.BaseURL() + "/router/complete"
	s.proxyToGateway(c, target, 120*time.Second)
}

// POST /v1/tools/execute — requires tools:exec
func (s *Server) handleToolsExecute(c *gin.Context) {
	if s.gw.Status() == gateway.StatusDown {
		errResponse(c, http.StatusServiceUnavailable,
			"service_unavailable",
			"Python router is not running. Host is in degraded mode.")
		return
	}

	target := s.gw.BaseURL() + "/tools/execute"
	s.proxyToGateway(c, target, 60*time.Second)
}

// --- Proxy helper ------------------------------------------------------------

var proxyClient = &http.Client{} // timeout set per-request via context

func (s *Server) proxyToGateway(c *gin.Context, targetURL string, timeout time.Duration) {
	ctx, cancel := context.WithTimeout(c.Request.Context(), timeout)
	defer cancel()

	body, err := io.ReadAll(c.Request.Body)
	if err != nil {
		errResponse(c, http.StatusBadRequest, "read_error", err.Error())
		return
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, targetURL, strings.NewReader(string(body)))
	if err != nil {
		errResponse(c, http.StatusInternalServerError, "proxy_error", err.Error())
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Forwarded-By", "navig-host")
	req.Header.Set("X-Request-Id", c.GetString("request_id"))

	resp, err := proxyClient.Do(req)
	if err != nil {
		errResponse(c, http.StatusBadGateway, "gateway_error", err.Error())
		return
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		errResponse(c, http.StatusBadGateway, "gateway_read_error", err.Error())
		return
	}

	c.Data(resp.StatusCode, "application/json", respBody)
}

// --- Response helpers --------------------------------------------------------

type errorBody struct {
	Error     string `json:"error"`
	Message   string `json:"message"`
	RequestID string `json:"request_id"`
}

func errResponse(c *gin.Context, status int, code, message string) {
	c.JSON(status, errorBody{
		Error:     code,
		Message:   message,
		RequestID: c.GetString("request_id"),
	})
}

func extractBearer(header string) string {
	const prefix = "Bearer "
	if len(header) > len(prefix) && strings.EqualFold(header[:len(prefix)], prefix) {
		return header[len(prefix):]
	}
	return ""
}

// generateRequestID produces a short unique request identifier.
func generateRequestID() string {
	b := make([]byte, 8)
	// crypto/rand not used here to keep hot-path fast; request IDs are not security-sensitive.
	_ = fastRandBytes(b)
	return fmt.Sprintf("%x", b)
}

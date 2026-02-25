package api

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/rs/cors"

	"navig-core/host/internal/auth"
	"navig-core/host/internal/config"
	"navig-core/host/internal/events"
)

// Server is the HTTP API server.
type Server struct {
	cfg      config.APIConfig
	auth     *auth.Manager
	bus      *events.Bus
	logger   *slog.Logger
	httpSrv  *http.Server
}

// NewServer constructs and configures the HTTP server but does not start it.
func NewServer(cfg config.APIConfig, auth *auth.Manager, bus *events.Bus, logger *slog.Logger) *Server {
	s := &Server{cfg: cfg, auth: auth, bus: bus, logger: logger}
	s.httpSrv = &http.Server{
		Addr:              cfg.Addr,
		Handler:           s.buildRouter(),
		ReadHeaderTimeout: 10 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       120 * time.Second,
	}
	return s
}

// ListenAndServe starts the HTTP server (blocking).
func (s *Server) ListenAndServe() error {
	s.logger.Info("api listening", "addr", s.cfg.Addr)
	return s.httpSrv.ListenAndServe()
}

// Shutdown gracefully stops the server with a 5-second deadline.
func (s *Server) Shutdown() {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = s.httpSrv.Shutdown(ctx)
}

// buildRouter wires all routes and middleware.
func (s *Server) buildRouter() http.Handler {
	r := chi.NewRouter()

	// Middleware
	r.Use(middleware.RequestID)
	r.Use(middleware.RealIP)
	r.Use(middleware.Recoverer)
	r.Use(middleware.Timeout(25 * time.Second))
	r.Use(s.loggingMiddleware)

	// CORS
	origins := s.cfg.AllowedOrigins
	if len(origins) == 0 {
		origins = []string{"vscode-webview://*"}
	}
	c := cors.New(cors.Options{
		AllowedOrigins:   origins,
		AllowedMethods:   []string{"GET", "POST", "DELETE", "OPTIONS"},
		AllowedHeaders:   []string{"Authorization", "Content-Type"},
		AllowCredentials: false,
	})
	r.Use(c.Handler)

	// Public routes
	r.Get("/health", s.handleHealth)
	r.Get("/metrics", promhttp.Handler().ServeHTTP)

	// Authenticated routes
	r.Group(func(r chi.Router) {
		r.Use(s.authMiddleware(auth.ScopeRead))
		r.Get("/status", s.handleStatus)
	})

	return r
}

// --- Handlers ---

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (s *Server) handleStatus(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"status":    "running",
		"timestamp": time.Now().UTC(),
	})
}

// --- Middleware ---

func (s *Server) loggingMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		ww := middleware.NewWrapResponseWriter(w, r.ProtoMajor)
		next.ServeHTTP(ww, r)
		s.logger.Debug("request",
			"method", r.Method,
			"path", r.URL.Path,
			"status", ww.Status(),
			"duration", time.Since(start),
		)
	})
}

func (s *Server) authMiddleware(required auth.Scope) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			token := r.Header.Get("Authorization")
			if len(token) > 7 && token[:7] == "Bearer " {
				token = token[7:]
			}
			claims, err := s.auth.ValidateToken(token)
			if err != nil || !auth.HasScope(claims, required) {
				writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

// --- Helpers ---

func writeJSON(w http.ResponseWriter, status int, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

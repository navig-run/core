// Package token manages cryptographically random bearer tokens with named
// scopes. Tokens are stored in the OS credential store (via go-keyring).
package token

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"go.uber.org/zap"
	"strings"
	"sync"

	"github.com/zalando/go-keyring"
)

// Scope represents a named permission.
type Scope string

// Well-known scopes.
const (
	ScopeInboxWrite Scope = "inbox:write"
	ScopeRouterCall Scope = "router:call"
	ScopeToolsExec  Scope = "tools:exec"
	ScopeAdmin      Scope = "admin" // grants all scopes
)

// ErrUnauthorized is returned when a token is missing, unknown, or lacks scope.
var ErrUnauthorized = errors.New("unauthorized")

// Entry is a stored token record.
type Entry struct {
	Name   string  `json:"name"`
	Token  string  `json:"token"`
	Scopes []Scope `json:"scopes"`
}

// HasScope returns true when e carries required or admin scope.
func (e *Entry) HasScope(required Scope) bool {
	for _, s := range e.Scopes {
		if s == required || s == ScopeAdmin {
			return true
		}
	}
	return false
}

// Store persists tokens using the OS keyring.
// The entire token map is stored as one JSON blob under a single keyring key.
type Store struct {
	mu      sync.RWMutex
	service string // keyring service name (e.g. "navig-host")
	account string // keyring account key (e.g. "tokens")
	logger  *zap.SugaredLogger
	cache   map[string]*Entry // keyed by raw token string
	backend Backend           // nil = use OS keyring
}

// NewStore creates a Store backed by the OS credential store.
func NewStore(service string, logger *zap.SugaredLogger) *Store {
	if logger == nil {
		logger = zap.NewNop().Sugar()
	}
	s := &Store{
		service: service,
		account: "tokens",
		logger:  logger,
		cache:   make(map[string]*Entry),
	}
	if err := s.load(); err != nil {
		logger.Warnw("token store: could not load existing tokens", "err", err)
	}
	return s
}

// loadFromBackend loads tokens from the injected Backend (used by NewStoreWithBackend).
func (s *Store) loadFromBackend() error {
	if s.backend == nil {
		return s.load()
	}
	entries, err := s.backend.Load()
	if err != nil {
		return err
	}
	for _, e := range entries {
		s.cache[e.Token] = e
	}
	return nil
}

// Create mints a new 256-bit random token, stores it, and returns the Entry.
func (s *Store) Create(name string, scopes []Scope) (*Entry, error) {
	if name == "" {
		return nil, errors.New("token: name must not be empty")
	}
	if len(scopes) == 0 {
		return nil, errors.New("token: at least one scope required")
	}

	raw, err := generateToken()
	if err != nil {
		return nil, fmt.Errorf("token: generate: %w", err)
	}

	e := &Entry{Name: name, Token: raw, Scopes: scopes}

	s.mu.Lock()
	defer s.mu.Unlock()

	// Check for duplicate name
	for _, existing := range s.cache {
		if existing.Name == name {
			return nil, fmt.Errorf("token: name %q already exists; revoke it first", name)
		}
	}

	s.cache[raw] = e
	if err := s.persist(); err != nil {
		delete(s.cache, raw)
		return nil, fmt.Errorf("token: persist: %w", err)
	}
	s.logger.Info("token created", "name", name, "scopes", scopes)
	return e, nil
}

// Revoke removes a token by client name.
func (s *Store) Revoke(name string) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	var found string
	for tok, e := range s.cache {
		if e.Name == name {
			found = tok
			break
		}
	}
	if found == "" {
		return fmt.Errorf("token: name %q not found", name)
	}
	delete(s.cache, found)
	if err := s.persist(); err != nil {
		return fmt.Errorf("token: persist after revoke: %w", err)
	}
	s.logger.Info("token revoked", "name", name)
	return nil
}

// List returns all stored token entries sorted by name.
func (s *Store) List() []*Entry {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]*Entry, 0, len(s.cache))
	for _, e := range s.cache {
		out = append(out, e)
	}
	return out
}

// Validate looks up a raw token string; returns ErrUnauthorized if not found.
func (s *Store) Validate(raw string) (*Entry, error) {
	if raw == "" {
		return nil, ErrUnauthorized
	}
	s.mu.RLock()
	e, ok := s.cache[raw]
	s.mu.RUnlock()
	if !ok {
		return nil, ErrUnauthorized
	}
	return e, nil
}

// ValidateWithScope validates the token and checks it carries required scope.
func (s *Store) ValidateWithScope(raw string, required Scope) (*Entry, error) {
	e, err := s.Validate(raw)
	if err != nil {
		return nil, err
	}
	if !e.HasScope(required) {
		return nil, fmt.Errorf("%w: missing scope %q", ErrUnauthorized, required)
	}
	return e, nil
}

// --- persistence --------------------------------------------------------------

func (s *Store) load() error {
	raw, err := keyring.Get(s.service, s.account)
	if err != nil {
		if isNotFound(err) {
			return nil // empty store — first run
		}
		return err
	}
	var entries []*Entry
	if err := json.Unmarshal([]byte(raw), &entries); err != nil {
		return fmt.Errorf("token: unmarshal: %w", err)
	}
	for _, e := range entries {
		s.cache[e.Token] = e
	}
	return nil
}

func (s *Store) persist() error {
	entries := make([]*Entry, 0, len(s.cache))
	for _, e := range s.cache {
		entries = append(entries, e)
	}
	if s.backend != nil {
		return s.backend.Save(entries)
	}
	data, err := json.Marshal(entries)
	if err != nil {
		return err
	}
	return keyring.Set(s.service, s.account, string(data))
}

// --- helpers ------------------------------------------------------------------

// generateToken returns a cryptographically random 256-bit hex token.
func generateToken() (string, error) {
	b := make([]byte, 32) // 256 bits
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}

// isNotFound checks whether a keyring error indicates "not found".
func isNotFound(err error) bool {
	if err == nil {
		return false
	}
	msg := strings.ToLower(err.Error())
	return strings.Contains(msg, "not found") ||
		strings.Contains(msg, "secret not found") ||
		strings.Contains(msg, "no item") ||
		err == keyring.ErrNotFound
}

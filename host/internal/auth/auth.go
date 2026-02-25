package auth

import (
	"errors"
	"fmt"
	"log/slog"
	"time"

	"github.com/golang-jwt/jwt/v5"

	"navig-core/host/internal/config"
)

// Scope represents an auth permission granted to a token.
type Scope string

const (
	ScopeRead   Scope = "read"
	ScopeWrite  Scope = "write"
	ScopeAdmin  Scope = "admin"
	ScopePlugin Scope = "plugin"
)

// Claims are the custom JWT claims used by the host.
type Claims struct {
	Scopes []Scope `json:"scopes"`
	jwt.RegisteredClaims
}

// SecretStore abstracts credential storage (keyring, env, file).
type SecretStore interface {
	Get(key string) (string, error)
	Set(key, value string) error
	Delete(key string) error
}

// Manager handles token issuance, validation, and revocation.
type Manager struct {
	cfg    config.AuthConfig
	store  SecretStore
	logger *slog.Logger
	secret []byte
}

// NewManager creates a new auth Manager.
func NewManager(cfg config.AuthConfig, store SecretStore, logger *slog.Logger) *Manager {
	return &Manager{
		cfg:    cfg,
		store:  store,
		logger: logger,
		secret: []byte(cfg.JWTSecret),
	}
}

// IssueToken creates a signed JWT with the given scopes.
func (m *Manager) IssueToken(subject string, scopes []Scope) (string, error) {
	if len(m.secret) == 0 {
		return "", errors.New("auth: jwt_secret not configured")
	}
	ttl := time.Duration(m.cfg.TokenTTLMins) * time.Minute
	if ttl == 0 {
		ttl = 60 * time.Minute
	}
	claims := Claims{
		Scopes: scopes,
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   subject,
			IssuedAt:  jwt.NewNumericDate(time.Now()),
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(ttl)),
		},
	}
	token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	return token.SignedString(m.secret)
}

// ValidateToken parses and validates a JWT string, returning the claims.
func (m *Manager) ValidateToken(tokenStr string) (*Claims, error) {
	token, err := jwt.ParseWithClaims(tokenStr, &Claims{}, func(t *jwt.Token) (interface{}, error) {
		if _, ok := t.Method.(*jwt.SigningMethodHMAC); !ok {
			return nil, fmt.Errorf("auth: unexpected signing method %v", t.Header["alg"])
		}
		return m.secret, nil
	})
	if err != nil {
		return nil, err
	}
	claims, ok := token.Claims.(*Claims)
	if !ok || !token.Valid {
		return nil, errors.New("auth: invalid token")
	}
	return claims, nil
}

// HasScope returns true if the claims include the requested scope.
func HasScope(claims *Claims, required Scope) bool {
	for _, s := range claims.Scopes {
		if s == required || s == ScopeAdmin {
			return true
		}
	}
	return false
}

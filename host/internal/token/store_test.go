package token_test

import (
	"strings"
	"testing"

	"go.uber.org/zap"
	"navig-core/host/internal/token"
)

// newMemStore returns a Store backed by an in-memory fake keyring so tests
// don't touch the real OS credential store.
func newMemStore(t *testing.T) *token.Store {
	t.Helper()
	logger := zap.NewNop()
	return token.NewStoreWithBackend(t.Name(), logger, token.NewMemBackend())
}

// --- Token validation --------------------------------------------------------

func TestValidTokenAccepted(t *testing.T) {
	s := newMemStore(t)
	e, err := s.Create("client", []token.Scope{token.ScopeRouterCall})
	if err != nil {
		t.Fatalf("create: %v", err)
	}

	got, err := s.Validate(e.Token)
	if err != nil {
		t.Fatalf("validate: %v", err)
	}
	if got.Name != "client" {
		t.Errorf("name: got %q, want %q", got.Name, "client")
	}
}

func TestInvalidTokenRejected(t *testing.T) {
	s := newMemStore(t)
	_, err := s.Validate("not-a-real-token")
	if err == nil {
		t.Fatal("expected error for invalid token, got nil")
	}
	if err != token.ErrUnauthorized {
		t.Errorf("expected ErrUnauthorized, got %v", err)
	}
}

func TestMissingTokenRejected(t *testing.T) {
	s := newMemStore(t)
	_, err := s.Validate("")
	if err != token.ErrUnauthorized {
		t.Errorf("expected ErrUnauthorized for empty token, got %v", err)
	}
}

// --- Scope enforcement -------------------------------------------------------

func TestScopeEnforcement(t *testing.T) {
	s := newMemStore(t)
	// Create a token with inbox:write only
	e, err := s.Create("inbox-only", []token.Scope{token.ScopeInboxWrite})
	if err != nil {
		t.Fatalf("create: %v", err)
	}

	// Should pass inbox:write
	if _, err := s.ValidateWithScope(e.Token, token.ScopeInboxWrite); err != nil {
		t.Errorf("inbox:write should be allowed: %v", err)
	}

	// Should fail router:call
	_, err = s.ValidateWithScope(e.Token, token.ScopeRouterCall)
	if err == nil {
		t.Fatal("router:call should be denied for inbox:write token")
	}
	if !strings.Contains(err.Error(), "missing scope") {
		t.Errorf("expected 'missing scope' error, got: %v", err)
	}
}

func TestAdminScopeGrantsAll(t *testing.T) {
	s := newMemStore(t)
	e, err := s.Create("superuser", []token.Scope{token.ScopeAdmin})
	if err != nil {
		t.Fatalf("create: %v", err)
	}

	for _, scope := range []token.Scope{
		token.ScopeInboxWrite,
		token.ScopeRouterCall,
		token.ScopeToolsExec,
	} {
		if _, err := s.ValidateWithScope(e.Token, scope); err != nil {
			t.Errorf("admin should grant scope %s: %v", scope, err)
		}
	}
}

// --- Duplicate name guard ----------------------------------------------------

func TestDuplicateNameRejected(t *testing.T) {
	s := newMemStore(t)
	if _, err := s.Create("dup", []token.Scope{token.ScopeInboxWrite}); err != nil {
		t.Fatalf("first create: %v", err)
	}
	_, err := s.Create("dup", []token.Scope{token.ScopeRouterCall})
	if err == nil {
		t.Fatal("expected error for duplicate name")
	}
}

// --- Revoke ------------------------------------------------------------------

func TestRevoke(t *testing.T) {
	s := newMemStore(t)
	e, _ := s.Create("temp", []token.Scope{token.ScopeInboxWrite})

	if err := s.Revoke("temp"); err != nil {
		t.Fatalf("revoke: %v", err)
	}
	if _, err := s.Validate(e.Token); err == nil {
		t.Fatal("token should be invalid after revoke")
	}
}

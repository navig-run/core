package token

import (
	"encoding/json"
	"sync"

	"go.uber.org/zap"
)

// Backend abstracts the persistence layer so tests can inject an in-memory store.
type Backend interface {
	Load() ([]*Entry, error)
	Save([]*Entry) error
}

// NewStoreWithBackend creates a Store using a custom backend (for testing).
// Pass nil for logger to use a no-op sugared logger.
func NewStoreWithBackend(service string, logger *zap.Logger, backend Backend) *Store {
	var sugar *zap.SugaredLogger
	if logger == nil {
		sugar = zap.NewNop().Sugar()
	} else {
		sugar = logger.Sugar()
	}
	s := &Store{
		service: service,
		account: "tokens",
		logger:  sugar,
		cache:   make(map[string]*Entry),
		backend: backend,
	}
	if err := s.loadFromBackend(); err != nil {
		sugar.Warnw("token store: could not load existing tokens", "err", err)
	}
	return s
}

// MemBackend is an in-memory Backend used in tests.
type MemBackend struct {
	mu   sync.Mutex
	data []byte
}

func NewMemBackend() *MemBackend { return &MemBackend{} }

func (m *MemBackend) Load() ([]*Entry, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if len(m.data) == 0 {
		return nil, nil
	}
	var entries []*Entry
	return entries, json.Unmarshal(m.data, &entries)
}

func (m *MemBackend) Save(entries []*Entry) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	b, err := json.Marshal(entries)
	if err != nil {
		return err
	}
	m.data = b
	return nil
}

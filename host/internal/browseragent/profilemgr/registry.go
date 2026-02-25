package profilemgr

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

type Registry struct {
	mu   *RWMutex
	path string
	dir  string
	data ProfileRegistrySchema
}

func NewRegistry() (*Registry, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return nil, fmt.Errorf("failed to get home dir: %w", err)
	}

	dir := filepath.Join(home, ".navig", "browser", "profiles")
	if err := os.MkdirAll(dir, 0700); err != nil {
		return nil, fmt.Errorf("failed to create profiles dir: %w", err)
	}

	regFile := filepath.Join(dir, "registry.json")
	r := &Registry{
		mu:   NewRWMutex(),
		path: regFile,
		dir:  dir,
		data: ProfileRegistrySchema{
			Records: make(map[ProfileID]ProfileRecord),
		},
	}

	if err := r.load(); err != nil {
		if os.IsNotExist(err) {
			r.seedDefaults()
			if err := r.save(); err != nil {
				return nil, err
			}
		} else {
			return nil, err
		}
	}

	return r, nil
}

func (r *Registry) load() error {
	b, err := os.ReadFile(r.path)
	if err != nil {
		return err
	}
	return json.Unmarshal(b, &r.data)
}

func (r *Registry) save() error {
	b, err := json.MarshalIndent(r.data, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(r.path, b, 0600)
}

func (r *Registry) seedDefaults() {
	now := time.Now().UTC()
	defaults := []ProfileID{"crypto", "social", "work"}

	for _, id := range defaults {
		r.data.Records[id] = ProfileRecord{
			ID:               id,
			Dir:              filepath.Join(r.dir, string(id)),
			PreferredEngine:  "auto",
			PreferredBrowser: "auto",
			Tags:             []string{string(id)},
			CreatedAt:        now,
			UpdatedAt:        now,
		}
	}
}

func (r *Registry) GetProfile(id ProfileID) (ProfileRecord, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	p, ok := r.data.Records[id]
	return p, ok
}

func (r *Registry) CreateProfile(record ProfileRecord) error {
	r.mu.Lock()
	defer r.mu.Unlock()

	// Registry concurrency coordination lock
	rl := NewRegistryLock(r.dir)
	if err := rl.Lock.AcquireWithTimeout(5*time.Second, 50*time.Millisecond); err != nil {
		return fmt.Errorf("failed to acquire registry lock: %w", err)
	}
	defer rl.Lock.Release()

	if err := r.load(); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("failed to reload registry: %w", err)
	}

	if err := record.Validate(); err != nil {
		return fmt.Errorf("validation failed: %w", err)
	}

	if _, exists := r.data.Records[record.ID]; exists {
		return fmt.Errorf("profile already exists: %s", record.ID)
	}

	if record.CreatedAt.IsZero() {
		record.CreatedAt = time.Now().UTC()
	}
	record.UpdatedAt = record.CreatedAt

	if record.Dir == "" {
		record.Dir = filepath.Join(r.dir, string(record.ID))
	}
	os.MkdirAll(record.Dir, 0700)

	r.data.Records[record.ID] = record
	return r.save()
}

func (r *Registry) UpdateProfile(record ProfileRecord) error {
	r.mu.Lock()
	defer r.mu.Unlock()

	rl := NewRegistryLock(r.dir)
	if err := rl.Lock.AcquireWithTimeout(5*time.Second, 50*time.Millisecond); err != nil {
		return fmt.Errorf("failed to acquire registry lock: %w", err)
	}
	defer rl.Lock.Release()

	if err := r.load(); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("failed to reload registry: %w", err)
	}

	if err := record.Validate(); err != nil {
		return fmt.Errorf("validation failed: %w", err)
	}

	existing, exists := r.data.Records[record.ID]
	if !exists {
		return fmt.Errorf("profile not found: %s", record.ID)
	}

	record.CreatedAt = existing.CreatedAt
	record.UpdatedAt = time.Now().UTC()
	if record.Dir == "" {
		record.Dir = existing.Dir
	}

	r.data.Records[record.ID] = record
	return r.save()
}

func (r *Registry) DeleteProfile(id ProfileID) error {
	r.mu.Lock()
	defer r.mu.Unlock()

	rl := NewRegistryLock(r.dir)
	if err := rl.Lock.AcquireWithTimeout(5*time.Second, 50*time.Millisecond); err != nil {
		return fmt.Errorf("failed to acquire registry lock: %w", err)
	}
	defer rl.Lock.Release()

	if err := r.load(); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("failed to reload registry: %w", err)
	}

	if _, exists := r.data.Records[id]; !exists {
		return fmt.Errorf("profile not found: %s", id)
	}

	delete(r.data.Records, id)
	return r.save()
}

func (r *Registry) ListProfiles() []ProfileRecord {
	r.mu.RLock()
	defer r.mu.RUnlock()

	list := make([]ProfileRecord, 0, len(r.data.Records))
	for _, p := range r.data.Records {
		list = append(list, p)
	}
	return list
}

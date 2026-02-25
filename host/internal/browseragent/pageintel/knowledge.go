package pageintel

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// ─────────────────────────────────────────────────────────────────────────────
// SelectorKnowledge — persistent selector evolution store
//
// When NavBrowser uses Tier 2 or Tier 3 to find a field (because Tier 1's
// cached selector broke), it writes the new winning selector here.
//
// Next run: Tier 1 loads this record, tries it first, and is fast again.
// The engine "learns" from every website change automatically.
//
// Storage: ~/.navig/selectors/<domain>.json
// ─────────────────────────────────────────────────────────────────────────────

// SelectorRecord holds one evolved selector for one field on one domain.
type SelectorRecord struct {
	Selector  string    `json:"selector"`  // winning CSS selector
	Tier      int       `json:"tier"`      // which tier found it
	Strategy  string    `json:"strategy"`  // e.g. "semantic_accessibility"
	UpdatedAt time.Time `json:"updatedAt"`
	HitCount  int       `json:"hitCount"`  // how many times this was used successfully
}

// DomainSelectors maps field semantic names to their records.
// e.g. {"username": SelectorRecord{...}, "password": SelectorRecord{...}}
type DomainSelectors map[string]SelectorRecord

// SelectorKnowledge is a thread-safe, file-backed selector evolution store.
type SelectorKnowledge struct {
	mu   sync.RWMutex
	dir  string
	data map[string]DomainSelectors // domain → field → record
}

// knowledgeBaseDir returns the default ~/.navig/selectors directory.
func knowledgeBaseDir() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".navig", "selectors")
}

// NewKnowledgeBase creates a SelectorKnowledge backed by the default dir.
// Existing records are loaded lazily on first access.
func NewKnowledgeBase() *SelectorKnowledge {
	return NewKnowledgeBaseAt(knowledgeBaseDir())
}

// NewKnowledgeBaseAt creates a SelectorKnowledge at a specific directory.
// Useful for tests.
func NewKnowledgeBaseAt(dir string) *SelectorKnowledge {
	return &SelectorKnowledge{
		dir:  dir,
		data: make(map[string]DomainSelectors),
	}
}

// domainFile returns the path for a domain's JSON store.
func (kb *SelectorKnowledge) domainFile(domain string) string {
	// Sanitize domain for use as filename
	safe := sanitizeDomain(domain)
	return filepath.Join(kb.dir, safe+".json")
}

func sanitizeDomain(domain string) string {
	var out []byte
	for _, c := range []byte(domain) {
		switch {
		case c >= 'a' && c <= 'z', c >= 'A' && c <= 'Z', c >= '0' && c <= '9', c == '-', c == '.':
			out = append(out, c)
		default:
			out = append(out, '_')
		}
	}
	return string(out)
}

// load reads the domain file from disk into memory (not thread-safe, call with lock held).
func (kb *SelectorKnowledge) load(domain string) {
	if _, ok := kb.data[domain]; ok {
		return // already loaded
	}
	b, err := os.ReadFile(kb.domainFile(domain))
	if err != nil {
		kb.data[domain] = make(DomainSelectors)
		return
	}
	var ds DomainSelectors
	if json.Unmarshal(b, &ds) == nil {
		kb.data[domain] = ds
	} else {
		kb.data[domain] = make(DomainSelectors)
	}
}

// flush writes the in-memory domain data to disk (not thread-safe, call with lock held).
func (kb *SelectorKnowledge) flush(domain string) error {
	ds, ok := kb.data[domain]
	if !ok {
		return nil
	}
	if err := os.MkdirAll(kb.dir, 0700); err != nil {
		return fmt.Errorf("knowledge: mkdir: %w", err)
	}
	b, err := json.MarshalIndent(ds, "", "  ")
	if err != nil {
		return fmt.Errorf("knowledge: marshal: %w", err)
	}
	return os.WriteFile(kb.domainFile(domain), b, 0600)
}

// Get retrieves the known selector for a field on a domain.
// Returns (record, found).
func (kb *SelectorKnowledge) Get(domain, field string) (SelectorRecord, bool) {
	kb.mu.Lock()
	defer kb.mu.Unlock()
	kb.load(domain)
	ds, ok := kb.data[domain]
	if !ok {
		return SelectorRecord{}, false
	}
	rec, ok := ds[field]
	return rec, ok
}

// Save persists a new or updated selector record for a domain+field.
// Called automatically by HealingFill/HealingClick when tier > 1.
func (kb *SelectorKnowledge) Save(domain, field string, rec SelectorRecord) error {
	kb.mu.Lock()
	defer kb.mu.Unlock()

	kb.load(domain)
	ds := kb.data[domain]

	// Update hit count if same selector
	if existing, ok := ds[field]; ok && existing.Selector == rec.Selector {
		rec.HitCount = existing.HitCount + 1
	} else {
		rec.HitCount = 1
	}
	rec.UpdatedAt = time.Now()

	ds[field] = rec
	kb.data[domain] = ds

	return kb.flush(domain)
}

// Delete removes a domain's knowledge (useful when a site does a full redesign).
func (kb *SelectorKnowledge) Delete(domain string) error {
	kb.mu.Lock()
	defer kb.mu.Unlock()
	delete(kb.data, domain)
	path := kb.domainFile(domain)
	if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("knowledge: delete: %w", err)
	}
	return nil
}

// ListDomains returns all domains with stored knowledge.
func (kb *SelectorKnowledge) ListDomains() ([]string, error) {
	entries, err := os.ReadDir(kb.dir)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	var domains []string
	for _, e := range entries {
		if !e.IsDir() && filepath.Ext(e.Name()) == ".json" {
			domains = append(domains, e.Name()[:len(e.Name())-5]) // strip .json
		}
	}
	return domains, nil
}

// LoadTier1Selectors is a convenience helper that loads all known selectors
// for a domain as TargetHint Tier1 values, ready for the resolver.
func (kb *SelectorKnowledge) LoadTier1Selectors(domain string) map[FieldSemantic]string {
	fields := []FieldSemantic{SemanticUsername, SemanticPassword, SemanticSubmit, SemanticCheckout, SemanticSearch}
	result := make(map[FieldSemantic]string)
	for _, f := range fields {
		if rec, ok := kb.Get(domain, string(f)); ok {
			result[f] = rec.Selector
		}
	}
	return result
}

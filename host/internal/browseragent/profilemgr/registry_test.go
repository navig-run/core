package profilemgr_test

import (
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"testing"

	"navig-core/host/internal/browseragent/profilemgr"
)

func setupTestRegistry(t *testing.T) (*profilemgr.Registry, string) {
	homeDir := filepath.Join(os.TempDir(), "navig_registry_test")
	os.Setenv("USERPROFILE", homeDir)
	os.Setenv("HOME", homeDir)
	os.MkdirAll(homeDir, 0700)

	reg, err := profilemgr.NewRegistry()
	if err != nil {
		t.Fatalf("failed to create registry: %v", err)
	}
	return reg, homeDir
}

func TestRegistryCRUD(t *testing.T) {
	reg, home := setupTestRegistry(t)
	defer os.RemoveAll(home)

	newProf := profilemgr.ProfileRecord{
		ID:               "test-crud",
		PreferredEngine:  "playwright",
		PreferredBrowser: "auto",
	}
	if err := reg.CreateProfile(newProf); err != nil {
		t.Fatalf("failed to create profile: %v", err)
	}

	prof, ok := reg.GetProfile("test-crud")
	if !ok {
		t.Fatalf("expected to find profile")
	}
	if prof.PreferredEngine != "playwright" {
		t.Fatalf("expected preferred engine playwright, got %s", prof.PreferredEngine)
	}

	prof.PreferredEngine = "chromedp"
	if err := reg.UpdateProfile(prof); err != nil {
		t.Fatalf("failed to update: %v", err)
	}
	prof, _ = reg.GetProfile("test-crud")
	if prof.PreferredEngine != "chromedp" {
		t.Fatalf("expected updated engine chromedp, got %s", prof.PreferredEngine)
	}

	if err := reg.DeleteProfile("test-crud"); err != nil {
		t.Fatalf("failed to delete: %v", err)
	}
	if _, ok := reg.GetProfile("test-crud"); ok {
		t.Fatalf("expected profile to be deleted")
	}
}

func TestRegistrySeedDefaults(t *testing.T) {
	reg, home := setupTestRegistry(t)
	defer os.RemoveAll(home)

	defaults := []profilemgr.ProfileID{"crypto", "social", "work"}
	for _, id := range defaults {
		if _, ok := reg.GetProfile(id); !ok {
			t.Errorf("expected default profile %s to exist", id)
		}
	}
}

func TestRegistryConcurrencyExtreme(t *testing.T) {
	reg, home := setupTestRegistry(t)
	defer os.RemoveAll(home)

	var wg sync.WaitGroup
	numWorkers := 100

	// We'll have 100 concurrent workers trying to create, update and delete a unique profile
	for i := 0; i < numWorkers; i++ {
		wg.Add(1)
		go func(workerID int) {
			defer wg.Done()

			profID := profilemgr.ProfileID(fmt.Sprintf("concurrent-test-%d", workerID))

			// 1. Create
			prof := profilemgr.ProfileRecord{
				ID:               profID,
				PreferredEngine:  "playwright",
				PreferredBrowser: "chrome",
			}
			if err := reg.CreateProfile(prof); err != nil {
				t.Errorf("Worker %d failed to create profile: %v", workerID, err)
				return
			}

			// 2. Read
			if _, ok := reg.GetProfile(profID); !ok {
				t.Errorf("Worker %d failed to get profile", workerID)
				return
			}

			// 3. Update
			prof.PreferredEngine = "chromedp"
			if err := reg.UpdateProfile(prof); err != nil {
				t.Errorf("Worker %d failed to update profile: %v", workerID, err)
				return
			}

			// 4. Delete
			if err := reg.DeleteProfile(profID); err != nil {
				t.Errorf("Worker %d failed to delete profile: %v", workerID, err)
			}
		}(i)
	}

	wg.Wait()

	// Ensure we only have defaults left
	profiles := reg.ListProfiles()
	if len(profiles) != 3 {
		t.Errorf("Expected 3 default profiles remaining, got %d", len(profiles))
	}
}

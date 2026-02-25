package profilemgr

import (
	"errors"
	"regexp"
	"time"
)

type ProfileID string

type ProfileRecord struct {
	ID               ProfileID `json:"id"`
	Dir              string    `json:"dir"`
	PreferredEngine  string    `json:"preferredEngine"`  // "chromedp" | "playwright" | "auto"
	PreferredBrowser string    `json:"preferredBrowser"` // "chrome" | "edge" | "auto"
	Tags             []string  `json:"tags"`
	CreatedAt        time.Time `json:"createdAt"`
	UpdatedAt        time.Time `json:"updatedAt"`
}

var validIDPattern = regexp.MustCompile(`^[a-zA-Z0-9_-]+$`)

func (p *ProfileRecord) Validate() error {
	if p.ID == "" {
		return errors.New("ProfileID cannot be empty")
	}
	if !validIDPattern.MatchString(string(p.ID)) {
		return errors.New("ProfileID contains invalid characters")
	}
	if p.PreferredEngine != "chromedp" && p.PreferredEngine != "playwright" && p.PreferredEngine != "auto" && p.PreferredEngine != "" {
		return errors.New("invalid PreferredEngine")
	}
	if p.PreferredBrowser != "chrome" && p.PreferredBrowser != "edge" && p.PreferredBrowser != "auto" && p.PreferredBrowser != "" {
		return errors.New("invalid PreferredBrowser")
	}
	return nil
}

type ProfileRegistrySchema struct {
	Records map[ProfileID]ProfileRecord `json:"records"`
}

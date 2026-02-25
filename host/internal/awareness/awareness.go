package awareness

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
)

// NodeIntelligence contains non-private system heuristics and context gathered automatically.
type NodeIntelligence struct {
	OS        string    `json:"os"`
	Arch      string    `json:"arch"`
	Hostname  string    `json:"hostname"`
	CPUs      int       `json:"cpus"`
	Bookmarks []Bookmark `json:"bookmarks"`
}

// Bookmark structure for ingested browser bookmarks.
type Bookmark struct {
	Title string `json:"title"`
	URL   string `json:"url"`
}

// ExtractNodeIntelligence crawls the local node to build an awareness context.
// It explicitly avoids private files and focuses on OS identity and standard browser bookmarks.
func ExtractNodeIntelligence() NodeIntelligence {
	host, _ := os.Hostname()
	
	intel := NodeIntelligence{
		OS:       runtime.GOOS,
		Arch:     runtime.GOARCH,
		Hostname: host,
		CPUs:     runtime.NumCPU(),
	}

	// Ingest basic bookmarks (Windows Chrome Example)
	intel.Bookmarks = extractChromeBookmarks()

	return intel
}

func extractChromeBookmarks() []Bookmark {
	var bmarks []Bookmark
	
	// Example Windows path for Chrome bookmarks
	userProfile := os.Getenv("USERPROFILE")
	if userProfile == "" {
		return bmarks
	}
	
	bookmarkPath := filepath.Join(userProfile, "AppData", "Local", "Google", "Chrome", "User Data", "Default", "Bookmarks")
	
	data, err := os.ReadFile(bookmarkPath)
	if err != nil {
		return bmarks
	}

	var root map[string]interface{}
	if err := json.Unmarshal(data, &root); err != nil {
		return bmarks
	}

	// Very basic traversal of the standard Chrome JSON bookmarks structure
	roots, ok := root["roots"].(map[string]interface{})
	if !ok {
		return bmarks
	}

	bookmarkBar, ok := roots["bookmark_bar"].(map[string]interface{})
	if ok {
		children, ok := bookmarkBar["children"].([]interface{})
		if ok {
			for _, child := range children {
				node, ok := child.(map[string]interface{})
				if !ok {
					continue
				}
				if typ, ok := node["type"].(string); ok && typ == "url" {
					urlObj, _ := node["url"].(string)
					titleObj, _ := node["name"].(string)
					bmarks = append(bmarks, Bookmark{
						Title: titleObj,
						URL:   urlObj,
					})
				}
			}
		}
	}

	return bmarks
}

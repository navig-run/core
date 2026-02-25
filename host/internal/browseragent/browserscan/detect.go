package browserscan

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"

	"navig-core/host/internal/browser"
)

func fileExists(path string) bool {
	info, err := os.Stat(path)
	if err != nil {
		return false
	}
	return !info.IsDir()
}

func getVersion(exe string) string {
	// Simple version extraction using standard commands
	var cmd *exec.Cmd
	if runtime.GOOS == "windows" {
		// on windows, parsing version is trickier from command line but we can attempt
		cmd = exec.Command("wmic", "datafile", "where", "name='"+strings.ReplaceAll(exe, "\\", "\\\\")+"'", "get", "Version", "/value")
	} else {
		cmd = exec.Command(exe, "--version")
	}
	out, err := cmd.Output()
	if err != nil {
		return "unknown"
	}
	ret := strings.TrimSpace(string(out))
	if runtime.GOOS == "windows" {
		parts := strings.Split(ret, "=")
		if len(parts) > 1 {
			return strings.TrimSpace(parts[1])
		}
		return "unknown"
	}

	// Linux/Mac: "Google Chrome 114.0.5735.90"
	parts := strings.Split(ret, " ")
	if len(parts) > 0 {
		return parts[len(parts)-1]
	}

	return ret
}

func Detect() []browser.BrowserInstall {
	var installs []browser.BrowserInstall

	var candidates []struct {
		Name browser.BrowserName
		Path string
	}

	if runtime.GOOS == "windows" {
		// Add default Program Files paths for Edge and Chrome
		pfs := []string{
			os.Getenv("ProgramFiles"),
			os.Getenv("ProgramFiles(x86)"),
			os.Getenv("LocalAppData"),
		}
		for _, pf := range pfs {
			if pf == "" {
				continue
			}
			candidates = append(candidates, struct {
				Name browser.BrowserName
				Path string
			}{browser.BrowserChrome, filepath.Join(pf, "Google", "Chrome", "Application", "chrome.exe")})
			candidates = append(candidates, struct {
				Name browser.BrowserName
				Path string
			}{browser.BrowserEdge, filepath.Join(pf, "Microsoft", "Edge", "Application", "msedge.exe")})
		}
	} else if runtime.GOOS == "darwin" {
		candidates = append(candidates, struct {
			Name browser.BrowserName
			Path string
		}{browser.BrowserChrome, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"})
		candidates = append(candidates, struct {
			Name browser.BrowserName
			Path string
		}{browser.BrowserEdge, "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"})
	} else {
		// Linux
		paths := []string{"/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/microsoft-edge"}
		for _, p := range paths {
			name := browser.BrowserChrome
			if strings.Contains(p, "edge") {
				name = browser.BrowserEdge
			} else if strings.Contains(p, "chromium") {
				name = browser.BrowserChromium
			}
			candidates = append(candidates, struct {
				Name browser.BrowserName
				Path string
			}{name, p})
		}
	}

	seen := make(map[string]bool)
	for _, c := range candidates {
		if fileExists(c.Path) && !seen[c.Path] {
			seen[c.Path] = true
			installs = append(installs, browser.BrowserInstall{
				Name:    c.Name,
				Path:    c.Path,
				Version: getVersion(c.Path),
			})
		}
	}

	return installs
}

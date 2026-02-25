package browser

// BrowserInstall describes a detected browser binary.
type BrowserInstall struct {
	Name    BrowserName
	Path    string
	Version string
}

// DriverOptions configures driver initialisation.
type DriverOptions struct {
	Driver      BrowserDriver
	BrowserPath string // explicit override; empty = auto-detect
	Headless    bool
	Args        []string
}

package config

import (
	"log/slog"
	"os"

	"github.com/knadh/koanf"
	"github.com/knadh/koanf/parsers/yaml"
	"github.com/knadh/koanf/providers/file"
)

// Config is the top-level configuration structure.
type Config struct {
	Log     LogConfig     `koanf:"log"`
	API     APIConfig     `koanf:"api"`
	Auth    AuthConfig    `koanf:"auth"`
	Plugins PluginsConfig `koanf:"plugins"`
	Keyring KeyringConfig `koanf:"keyring"`
}

type LogConfig struct {
	Level  string `koanf:"level"`  // debug, info, warn, error
	Format string `koanf:"format"` // text, json
}

type APIConfig struct {
	Addr           string   `koanf:"addr"`            // default: 127.0.0.1:4747
	AllowedOrigins []string `koanf:"allowed_origins"` // CORS origins
}

type AuthConfig struct {
	JWTSecret    string `koanf:"jwt_secret"`
	TokenTTLMins int    `koanf:"token_ttl_mins"` // default: 60
}

type PluginsConfig struct {
	Dir        string `koanf:"dir"`         // path to plugins directory
	PythonPath string `koanf:"python_path"` // python binary (default: python3)
}

type KeyringConfig struct {
	Service string `koanf:"service"` // default: navig-host
}

// defaults fills in sensible defaults before loading the config file.
func defaults(k *koanf.Koanf) {
	_ = k.Load(konfMap(map[string]interface{}{
		"log.level":              "info",
		"log.format":             "text",
		"api.addr":               "127.0.0.1:4747",
		"api.allowed_origins":    []string{"vscode-webview://*"},
		"auth.token_ttl_mins":    60,
		"plugins.python_path":    "python3",
		"keyring.service":        "navig-host",
	}), nil)
}

// Load reads config from cfgPath (may be empty → look for ~/.navig/config.yaml).
func Load(cfgPath string) (*Config, error) {
	k := koanf.New(".")
	defaults(k)

	if cfgPath == "" {
		home, _ := os.UserHomeDir()
		cfgPath = home + "/.navig/config.yaml"
	}

	if _, err := os.Stat(cfgPath); err == nil {
		if err := k.Load(file.Provider(cfgPath), yaml.Parser()); err != nil {
			return nil, err
		}
	}

	var cfg Config
	if err := k.Unmarshal("", &cfg); err != nil {
		return nil, err
	}
	return &cfg, nil
}

// NewLogger returns a structured slog.Logger based on config.
func NewLogger(cfg *Config) *slog.Logger {
	level := slog.LevelInfo
	switch cfg.Log.Level {
	case "debug":
		level = slog.LevelDebug
	case "warn":
		level = slog.LevelWarn
	case "error":
		level = slog.LevelError
	}

	opts := &slog.HandlerOptions{Level: level}
	var handler slog.Handler
	if cfg.Log.Format == "json" {
		handler = slog.NewJSONHandler(os.Stdout, opts)
	} else {
		handler = slog.NewTextHandler(os.Stdout, opts)
	}
	return slog.New(handler)
}

// konfMap is a simple in-memory koanf provider for defaults.
type konfMap map[string]interface{}

func (m konfMap) ReadBytes() ([]byte, error)              { return nil, nil }
func (m konfMap) Read() (map[string]interface{}, error)   { return m, nil }
func (m konfMap) Watch(_ func(event interface{}, err error)) error { return nil }

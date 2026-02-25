package gateway

import (
	"errors"
	"fmt"
	"go.uber.org/zap"
	"os"
	"os/exec"
)

// resolvePython finds the Python interpreter according to the discovery order:
//  1. config value (non-empty)
//  2. NAVIG_PYTHON environment variable
//  3. python3 in PATH
//  4. python in PATH
func resolvePython(cfgPath string) (string, error) {
	candidates := []string{}
	if cfgPath != "" {
		candidates = append(candidates, cfgPath)
	}
	if env := os.Getenv("NAVIG_PYTHON"); env != "" {
		candidates = append(candidates, env)
	}
	candidates = append(candidates, "python3", "python")

	for _, c := range candidates {
		if path, err := exec.LookPath(c); err == nil {
			return path, nil
		}
	}
	return "", errors.New("gateway: no Python interpreter found; set python_path in config or NAVIG_PYTHON env var")
}

// validateNavigPackage checks that `python -c "import navig"` succeeds.
func validateNavigPackage(python string, logger *zap.SugaredLogger) error {
	out, err := exec.Command(python, "-c", "import navig").CombinedOutput()
	if err != nil {
		return fmt.Errorf("import navig failed: %w — output: %s", err, string(out))
	}
	logger.Debug("gateway: navig package validated", "python", python)
	return nil
}

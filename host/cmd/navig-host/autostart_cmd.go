package main

import (
	"fmt"
	"runtime"

	"github.com/spf13/cobra"

	hostOS "navig-core/host/internal/os"
)

var autostartCmd = &cobra.Command{
	Use:   "autostart",
	Short: "Manage host auto-start on login",
}

var autostartInstallCmd = &cobra.Command{
	Use:   "install",
	Short: "Register navig-host to start on user login",
	RunE: func(cmd *cobra.Command, args []string) error {
		paths := hostOS.NewPaths()
		if err := paths.RegisterAutostart(); err != nil {
			return fmt.Errorf("autostart install failed: %w", err)
		}
		fmt.Printf("Auto-start installed (%s).\n", platformAutoStartDetail())
		return nil
	},
}

var autostartUninstallCmd = &cobra.Command{
	Use:   "uninstall",
	Short: "Remove navig-host auto-start registration",
	RunE: func(cmd *cobra.Command, args []string) error {
		paths := hostOS.NewPaths()
		if err := paths.RemoveAutostart(); err != nil {
			return fmt.Errorf("autostart uninstall failed: %w", err)
		}
		fmt.Println("Auto-start removed.")
		return nil
	},
}

var autostartStatusCmd = &cobra.Command{
	Use:   "status",
	Short: "Show current auto-start status",
	RunE: func(cmd *cobra.Command, args []string) error {
		paths := hostOS.NewPaths()
		enabled, detail, err := paths.AutostartStatus()
		if err != nil {
			return fmt.Errorf("autostart status: %w", err)
		}
		if enabled {
			fmt.Printf("Auto-start: ENABLED\n%s\n", detail)
		} else {
			fmt.Println("Auto-start: DISABLED")
		}
		return nil
	},
}

func init() {
	autostartCmd.AddCommand(autostartInstallCmd, autostartUninstallCmd, autostartStatusCmd)
	rootCmd.AddCommand(autostartCmd)
}

func platformAutoStartDetail() string {
	switch runtime.GOOS {
	case "windows":
		return `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
	case "darwin":
		return "~/Library/LaunchAgents/com.navig.core-host.plist"
	default:
		return "~/.config/systemd/user/navig-core-host.service"
	}
}

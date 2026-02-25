package main

import (
	"fmt"
	"strings"

	"github.com/spf13/cobra"

	"navig-core/host/internal/logging"
	"navig-core/host/internal/token"
)

var tokenCmd = &cobra.Command{
	Use:   "token",
	Short: "Manage authentication tokens",
}

var tokenCreateCmd = &cobra.Command{
	Use:   "create",
	Short: "Create a new bearer token",
	Example: `  navig-host token create --name "vscode-extension" --scopes router:call,inbox:write
  navig-host token create --name "chrome" --scopes inbox:write`,
	RunE: func(cmd *cobra.Command, args []string) error {
		name, _ := cmd.Flags().GetString("name")
		scopesRaw, _ := cmd.Flags().GetString("scopes")

		if name == "" {
			return fmt.Errorf("--name is required")
		}
		if scopesRaw == "" {
			return fmt.Errorf("--scopes is required (e.g. inbox:write,router:call)")
		}

		scopes := parseScopes(scopesRaw)
		if len(scopes) == 0 {
			return fmt.Errorf("no valid scopes parsed from %q", scopesRaw)
		}

		logger, _ := logging.New(logging.LogDirForOS(), "info")
		store := token.NewStore("navig-host", logger.Sugar())

		entry, err := store.Create(name, scopes)
		if err != nil {
			return err
		}

		fmt.Printf("Token created for %q\n", entry.Name)
		fmt.Printf("Scopes : %s\n", formatScopes(entry.Scopes))
		fmt.Printf("\nToken  : %s\n", entry.Token)
		fmt.Println("\nStore this token securely — it will not be shown again.")
		return nil
	},
}

var tokenRevokeCmd = &cobra.Command{
	Use:   "revoke",
	Short: "Revoke a token by client name",
	Example: `  navig-host token revoke --name "chrome"`,
	RunE: func(cmd *cobra.Command, args []string) error {
		name, _ := cmd.Flags().GetString("name")
		if name == "" {
			return fmt.Errorf("--name is required")
		}

		logger, _ := logging.New(logging.LogDirForOS(), "info")
		store := token.NewStore("navig-host", logger.Sugar())

		if err := store.Revoke(name); err != nil {
			return err
		}
		fmt.Printf("Token for %q has been revoked.\n", name)
		return nil
	},
}

var tokenListCmd = &cobra.Command{
	Use:   "list",
	Short: "List all known tokens",
	RunE: func(cmd *cobra.Command, args []string) error {
		logger, _ := logging.New(logging.LogDirForOS(), "info")
		store := token.NewStore("navig-host", logger.Sugar())

		entries := store.List()
		if len(entries) == 0 {
			fmt.Println("No tokens stored.")
			return nil
		}
		fmt.Printf("%-20s  %-40s  %s\n", "NAME", "TOKEN (partial)", "SCOPES")
		fmt.Println(strings.Repeat("-", 78))
		for _, e := range entries {
			partial := e.Token
			if len(partial) > 12 {
				partial = partial[:8] + "…" + partial[len(partial)-4:]
			}
			fmt.Printf("%-20s  %-40s  %s\n", e.Name, partial, formatScopes(e.Scopes))
		}
		return nil
	},
}

func init() {
	tokenCreateCmd.Flags().String("name", "", "Client name (e.g. vscode-extension)")
	tokenCreateCmd.Flags().String("scopes", "", "Comma-separated scopes: inbox:write,router:call,tools:exec,admin")

	tokenRevokeCmd.Flags().String("name", "", "Client name to revoke")

	tokenCmd.AddCommand(tokenCreateCmd, tokenRevokeCmd, tokenListCmd)
	rootCmd.AddCommand(tokenCmd)
}

func parseScopes(raw string) []token.Scope {
	parts := strings.Split(raw, ",")
	out := make([]token.Scope, 0, len(parts))
	valid := map[token.Scope]bool{
		token.ScopeInboxWrite: true,
		token.ScopeRouterCall: true,
		token.ScopeToolsExec:  true,
		token.ScopeAdmin:      true,
	}
	for _, p := range parts {
		s := token.Scope(strings.TrimSpace(p))
		if valid[s] {
			out = append(out, s)
		}
	}
	return out
}

func formatScopes(scopes []token.Scope) string {
	ss := make([]string, len(scopes))
	for i, s := range scopes {
		ss[i] = string(s)
	}
	return strings.Join(ss, ", ")
}

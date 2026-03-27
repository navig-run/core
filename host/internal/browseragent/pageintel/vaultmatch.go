package pageintel

import (
	"strings"

	"navig-core/host/internal/vault"
)

// VaultMatch evaluates the semantic analysis of a page and returns a map of
// optimal CSS selectors to their corresponding string values from the Operator's Vault.
// This allows zero-shot, instantaneous and secure native form filling, without
// exposing raw Vault data to an external LLM.
func VaultMatch(analysis *PageAnalysis, v *vault.Vault) map[string]string {
	fills := make(map[string]string)

	if v == nil || analysis == nil {
		return fills
	}

	for _, inp := range analysis.Inputs {
		// Try to match based on input heuristics
		val := matchInputToVault(&inp, v, analysis)
		if val != "" && inp.Selector != "" {
			fills[inp.Selector] = val
		}
	}

	return fills
}

// matchInputToVault uses heuristics on the name, id, placeholder, and label
// of an input to find the best matching field in the Vault.
func matchInputToVault(inp *InputInfo, v *vault.Vault, analysis *PageAnalysis) string {
	combined := strings.ToLower(inp.Name + " " + inp.ID + " " + inp.Placeholder + " " + inp.Label)

	// --- 0. Login / Account Matches ---
	// If the user has saved accounts, try to map username/password based on the Domain
	for _, acc := range v.Accounts {
		if analysis.URL != "" && acc.Domain != "" && strings.Contains(strings.ToLower(analysis.URL), strings.ToLower(acc.Domain)) {
			// It's the right domain. Is it the username or the password?
			if inp.Type == "password" || containsAny(combined, "password", "pass", "pwd", "secret") {
				return acc.Password
			}
			if containsAny(combined, "username", "login", "user", "email", "account") {
				return acc.Username
			}
		}
	}

	// --- 1. Identity Matches ---
	if containsAny(combined, "first name", "fname", "given name") {
		return v.Identity.FirstName
	}
	if containsAny(combined, "last name", "lname", "surname", "family name") {
		return v.Identity.LastName
	}
	// Fallback for generic "Name" (if we couldn't separate first/last)
	if containsAny(combined, "name", "full name") && !containsAny(combined, "card", "user", "company") {
		if v.Identity.FirstName != "" && v.Identity.LastName != "" {
			return v.Identity.FirstName + " " + v.Identity.LastName
		}
		return v.Identity.FirstName
	}
	if inp.Type == "email" || containsAny(combined, "email", "e-mail") {
		return v.Identity.Email
	}
	if inp.Type == "tel" || containsAny(combined, "phone", "mobile", "telephone", "cell") {
		return v.Identity.Phone
	}
	if containsAny(combined, "company", "organization", "business name") {
		return v.Identity.Company
	}

	// --- 2. Address Matches (Defaulting to Shipping for now) ---
	addr := v.ShippingAddress // Could be enhanced to distinguish Billing vs Shipping based on form container Context

	if containsAny(combined, "address 1", "address line 1", "street address") {
		return addr.Line1
	}
	if containsAny(combined, "address 2", "address line 2", "apt", "suite", "building") {
		return addr.Line2
	}
	if containsAny(combined, "city", "town") {
		return addr.City
	}
	if containsAny(combined, "state", "province", "region") {
		return addr.State
	}
	if containsAny(combined, "zip", "postal", "postcode") {
		return addr.PostalCode
	}
	if containsAny(combined, "country", "nation") {
		return addr.Country
	}

	// --- 3. Payment Matches (Using the first profile if available) ---
	if len(v.PaymentProfiles) > 0 {
		card := v.PaymentProfiles[0]

		if containsAny(combined, "card number", "card no", "cc number", "pan") {
			return card.CardNumber
		}
		if containsAny(combined, "name on card", "cardholder", "card name") {
			return card.NameOnCard
		}
		if containsAny(combined, "cvv", "cvc", "security code") {
			return card.CVV
		}

		// Month/Year edge cases (single inputs vs double inputs)
		if containsAny(combined, "exp month", "expiration month") {
			return card.ExpiryMonth
		}
		if containsAny(combined, "exp year", "expiration year") {
			return card.ExpiryYear
		}
		if containsAny(combined, "expiry", "expiration date", "valid thru") {
			return card.ExpiryMonth + "/" + card.ExpiryYear
		}
	}

	return ""
}

// containsAny is a helper to check if a target string contains any of the required substrings.
func containsAny(s string, substrings ...string) bool {
	for _, sub := range substrings {
		if strings.Contains(s, sub) {
			return true
		}
	}
	return false
}

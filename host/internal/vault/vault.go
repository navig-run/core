package vault

// Identity represents the personal details of the operator.
type Identity struct {
	FirstName string `json:"firstName" yaml:"firstName"`
	LastName  string `json:"lastName" yaml:"lastName"`
	Email     string `json:"email" yaml:"email"`
	Phone     string `json:"phone" yaml:"phone"`
	Company   string `json:"company,omitempty" yaml:"company,omitempty"`
}

// Address represents a physical address.
type Address struct {
	Line1      string `json:"line1" yaml:"line1"`
	Line2      string `json:"line2,omitempty" yaml:"line2,omitempty"`
	City       string `json:"city" yaml:"city"`
	State      string `json:"state" yaml:"state"`
	PostalCode string `json:"postalCode" yaml:"postalCode"`
	Country    string `json:"country" yaml:"country"`
}

// PaymentProfile represents a masked payment option.
type PaymentProfile struct {
	Alias       string `json:"alias" yaml:"alias"`             // e.g. "Personal Visa"
	NameOnCard  string `json:"nameOnCard" yaml:"nameOnCard"`
	CardNumber  string `json:"cardNumber" yaml:"-"`            // Never logged/serialized to LLM
	ExpiryMonth string `json:"expiryMonth" yaml:"expiryMonth"` // "MM"
	ExpiryYear  string `json:"expiryYear" yaml:"expiryYear"`   // "YYYY" or "YY"
	CVV         string `json:"cvv" yaml:"-"`                   // Never logged/serialized to LLM
}

// AccountProfile represents a credential pair for a specific domain.
type AccountProfile struct {
	Domain   string `json:"domain" yaml:"domain"`
	Username string `json:"username" yaml:"username"`
	Password string `json:"password" yaml:"-"` // Never logged/serialized to LLM
}

// Vault represents the decrypted in-memory operator vault.
type Vault struct {
	Identity        Identity         `json:"identity" yaml:"identity"`
	ShippingAddress Address          `json:"shippingAddress" yaml:"shippingAddress"`
	BillingAddress  Address          `json:"billingAddress" yaml:"billingAddress"`
	PaymentProfiles []PaymentProfile `json:"paymentProfiles" yaml:"paymentProfiles"`
	Accounts        []AccountProfile `json:"accounts" yaml:"accounts"`
}

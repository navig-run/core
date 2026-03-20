---
name: fin-ledger
description: Sovereign financial tracking using plaintext accounting (hledger/beancount).
metadata:
  navig:
    emoji: 💰
    requires:
      bins: [hledger]
      files: [main.ledger]
---

# Finance Ledger Skill

Manage personal finances using the powerful, future-proof plaintext accounting method. This skill allows you to record transactions, check balances, and track budgets using `hledger`.

## Core Actions

### 1. Record Transaction
Append a new transaction to the journal.

**Command:**
```bash
# Format: Date Description  Account1  Amount  Account2
echo "2024-03-20 Grocery Store  Expenses:Food:Groceries  $50.00  Assets:Bank" >> main.ledger
```

**Agent Tip**: When a user says "I spent $50 on groceries", convert it to the Ledger format above.

### 2. Check Balances
View current processing of all accounts.

**Command:**
```bash
# Show balance report
hledger bal

# Show specific account (e.g., Food expenses)
hledger bal Expenses:Food
```

### 3. Register View
See the history of transactions for an account.

**Command:**
```bash
# List last 10 transactions for Bank
hledger reg Assets:Bank --tail 10
```

### 4. Budget Status
Check performance against defined monthly budgets.

**Command:**
```bash
# Requires --budget flag and defined budget rules in ledger file
hledger bal --budget
```

## Templates

### `main.ledger` Starter
```ledger
; Journal file for personal finance

; --- Accounts Configuration ---
account Assets:Bank
account Assets:Cash
account Liabilities:CreditCard
account Expenses:Food:Groceries
account Expenses:Food:Dining
account Expenses:Rent
account Income:Salary

; --- Budget Rules ---
~ Monthly
    Expenses:Food:Groceries  $400
    Expenses:Food:Dining     $150

; --- Opening Balances ---
2024-01-01 * Opening Balance
    Assets:Bank          $5000.00
    Equity:OpeningBalances
```

## Best Practices

1. **Date Format**: Always use YYYY-MM-DD.
2. **Double Entry**: Every transaction must balance (Total = 0).
3. **Hierarchy**: Use colons for categories (e.g., `Expenses:Food:Dining`).
4. **Currency**: Be consistent with symbols ($ vs USD).




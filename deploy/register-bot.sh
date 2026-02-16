#!/bin/bash
# Register the navig-bot user on Conduit
RESULT=$(curl -s -X POST http://localhost:6167/_matrix/client/v3/register \
    -H "Content-Type: application/json" \
    -d '{
        "username": "navig-bot",
        "password": "navig-matrix-bot-2026",
        "auth": {"type": "m.login.dummy"},
        "inhibit_login": false
    }' 2>&1)

echo "Registration result:"
echo "$RESULT" | python3 -m json.tool 2>/dev/null || echo "$RESULT"

if echo "$RESULT" | grep -q "user_id"; then
    echo "SUCCESS: Bot user registered"
elif echo "$RESULT" | grep -q "M_USER_IN_USE"; then
    echo "OK: Bot user already exists"
else
    echo "Check result above for details"
fi

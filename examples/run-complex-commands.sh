# Simple commands still work as before
navig run "ls -la"

# Complex commands: use --file (recommended)
navig run --file my_script.sh

# Or pipe via stdin
cat script.sh | navig run --stdin

# PowerShell here-strings work great with stdin
@'
cat > config.json << 'EOF'
{"api_key": "xyz", "url": "https://example.com"}
EOF
'@ | navig run --stdin
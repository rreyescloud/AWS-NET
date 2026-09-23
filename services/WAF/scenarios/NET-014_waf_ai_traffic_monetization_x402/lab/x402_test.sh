#!/bin/bash
# x402 monetization lab — decode the 402 payment challenge
URL="https://<distribution-domain>.cloudfront.net/documents/dr-test-file.txt"

echo "=== 1) Status code for GPTBot (expect 402) ==="
curl -s -o /dev/null -w "HTTP %{http_code}\n" -A "GPTBot/1.2" "$URL"

echo ""
echo "=== 2) Decoded x402 payment challenge ==="
curl -si -A "GPTBot/1.2" "$URL" \
  | awk 'tolower($1)=="payment-required:"{print $2}' \
  | tr -d '\r' \
  | python3 -c "import sys,base64,json;raw=sys.stdin.read().strip();raw+='='*(-len(raw)%4);print(json.dumps(json.loads(base64.b64decode(raw)),indent=2))"

echo ""
echo "=== 3) Normal browser for contrast (expect 403, no 402) ==="
curl -s -o /dev/null -w "HTTP %{http_code}\n" -A "Mozilla/5.0 (Macintosh) Safari/605.1.15" "$URL"

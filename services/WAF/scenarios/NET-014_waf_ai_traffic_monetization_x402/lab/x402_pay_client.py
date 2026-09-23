#!/usr/bin/env python3
"""
NET-014 — WAF AI Traffic Monetization (x402) lab.

Minimal x402 payment client that completes the full monetization loop against a
CloudFront distribution protected by an AWS WAF Web ACL with a `Monetize` rule:

    GET (as an AI bot) -> HTTP 402 payment challenge
    sign EIP-3009 authorization (gasless for the payer)
    resubmit with PAYMENT-SIGNATURE header -> HTTP 200 + content
    facilitator settles the USDC micropayment on-chain (Base Sepolia)

No Bedrock AgentCore, no CDK, no Docker required — just the official `x402`
library plus `eth-account` / `web3`.

Setup (isolated venv; system Python 3.14 breaks some deps):
    python3 -m venv .venv && . .venv/bin/activate
    pip install "x402" eth-account web3 requests

Testnet only (Base Sepolia). The payer key is read from the environment so it
never lands in source or git history — NEVER put a mainnet private key here.
Fund the PAYER wallet with testnet USDC at https://faucet.circle.com (select
Base Sepolia), then:

    export X402_PAYER_KEY=0x<throwaway testnet key>
"""
import base64
import json
import os
import sys

import requests
from eth_account import Account
from web3 import Web3
from x402.client import x402ClientSync
from x402.mechanisms.evm.exact.register import register_exact_evm_client
from x402.mechanisms.evm.signers import EthAccountSigner
from x402.http import x402HTTPClientSync

# --- Config (replace with your own values) ---------------------------------
URL = "https://<your-distribution>.cloudfront.net/documents/premium.txt"
# The PAYER (bot) wallet — MUST be different from the recipient/payTo wallet,
# or the facilitator rejects with "self_send_not_allowed".
PAYER_PRIVATE_KEY = os.environ.get("X402_PAYER_KEY")
RECIPIENT_ADDRESS = "0x<PAYTO_WALLET_CONFIGURED_IN_WAF_CONSOLE>"
USDC_BASE_SEPOLIA = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
RPC = "https://sepolia.base.org"
BOT_UA = "GPTBot/1.2 (+https://openai.com/gptbot)"
# ---------------------------------------------------------------------------

w3 = Web3(Web3.HTTPProvider(RPC))
_erc20 = w3.eth.contract(
    address=Web3.to_checksum_address(USDC_BASE_SEPOLIA),
    abi=[{"constant": True, "inputs": [{"name": "a", "type": "address"}],
          "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
          "type": "function"}],
)


def usdc(addr: str) -> float:
    return _erc20.functions.balanceOf(Web3.to_checksum_address(addr)).call() / 1e6


def main() -> None:
    if not PAYER_PRIVATE_KEY:
        sys.exit("X402_PAYER_KEY is not set. Export the throwaway testnet key of the payer wallet.")
    acct = Account.from_key(PAYER_PRIVATE_KEY)
    print(f"Payer (bot)      : {acct.address}")
    print(f"Recipient (payTo): {RECIPIENT_ADDRESS}")
    print(f"Balances BEFORE  : payer={usdc(acct.address)}  recipient={usdc(RECIPIENT_ADDRESS)}\n")

    # Build an x402 client with our EVM signer registered for the `exact` scheme.
    client = x402ClientSync()
    register_exact_evm_client(client, EthAccountSigner(acct))
    http = x402HTTPClientSync(client)
    ua = {"User-Agent": BOT_UA}

    # 1) Initial request -> expect a 402 challenge from WAF.
    r1 = requests.get(URL, headers=ua)
    print(f"[1] GET (as bot)      -> HTTP {r1.status_code}   (payment challenge)")
    if r1.status_code != 402:
        print("[!] Expected 402. Body:", r1.text[:300])
        return

    # 2) Parse the challenge and sign the EIP-3009 authorization.
    pay_headers, _payload = http.handle_402_response(dict(r1.headers), r1.content)
    print(f"[2] Signed EIP-3009   -> attaching header: {list(pay_headers.keys())}")

    # 3) Resubmit with the payment proof -> WAF verifies, settles, serves 200.
    r2 = requests.get(URL, headers={**ua, **pay_headers})
    print(f"[3] GET + PAYMENT     -> HTTP {r2.status_code} | server: {r2.headers.get('Server')}")

    for k, v in r2.headers.items():
        if k.lower() == "payment-response":
            d = json.loads(base64.b64decode(v + "=" * (-len(v) % 4)))
            print(f"    settlement success: {d.get('success')}")
            print(f"    on-chain tx       : {d.get('transaction')}")
            print(f"    explorer          : https://sepolia.basescan.org/tx/{d.get('transaction')}")

    print(f"\nContent served: {r2.text.strip()[:120]}")
    print(f"Balances AFTER   : payer={usdc(acct.address)}  recipient={usdc(RECIPIENT_ADDRESS)}")
    print("\n[OK] 402 -> pay -> 200" if r2.status_code == 200 else f"[!] status {r2.status_code}")


if __name__ == "__main__":
    main()

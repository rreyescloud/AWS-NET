#!/usr/bin/env python3
"""
Level 3 of the WAF AI-traffic-monetization lab: full x402 pay -> 200 loop.
Run:  X402_PAYER_KEY=0x... /tmp/ethvenv/bin/python "x402-level3.py"
Testnet only (Base Sepolia). Wallets are throwaway testnet keys, but the payer
key is still read from the environment so it never lands in git history.
"""
import os
import sys
import requests, base64, json
from eth_account import Account
from web3 import Web3
from x402.client import x402ClientSync
from x402.mechanisms.evm.exact.register import register_exact_evm_client
from x402.mechanisms.evm.signers import EthAccountSigner
from x402.http import x402HTTPClientSync

URL   = "https://<distribution-domain>.cloudfront.net/documents/premium.txt"
PAYER = os.environ.get("X402_PAYER_KEY")  # bot wallet (Base Sepolia testnet)
if not PAYER:
    sys.exit("X402_PAYER_KEY is not set. Export the throwaway testnet key of the payer wallet.")
USDC  = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
RECIP = "0x544D33E70331056B8283311B29B723D8B912Fc00"
w3 = Web3(Web3.HTTPProvider("https://sepolia.base.org"))
erc20 = w3.eth.contract(address=Web3.to_checksum_address(USDC), abi=[{"constant":True,"inputs":[{"name":"a","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}])
def bal(a): return erc20.functions.balanceOf(Web3.to_checksum_address(a)).call()/1e6

acct = Account.from_key(PAYER)
print(f"Payer (bot)      : {acct.address}")
print(f"Recipient (payTo): {RECIP}")
print(f"Balances BEFORE  : payer={bal(acct.address)}  recipient={bal(RECIP)}\n")

client = x402ClientSync(); register_exact_evm_client(client, EthAccountSigner(acct))
http = x402HTTPClientSync(client)
UA = {"User-Agent": "GPTBot/1.2"}

r1 = requests.get(URL, headers=UA)
print(f"[1] GET (as GPTBot)        -> HTTP {r1.status_code}   (payment challenge)")
pay, _ = http.handle_402_response(dict(r1.headers), r1.content)
print(f"[2] Signed EIP-3009 auth   -> attaching header: {list(pay.keys())}")
r2 = requests.get(URL, headers={**UA, **pay})
print(f"[3] GET + PAYMENT          -> HTTP {r2.status_code}")
pr = [v for k,v in r2.headers.items() if k.lower()=="payment-response"]
if pr:
    d = json.loads(base64.b64decode(pr[0] + "="*(-len(pr[0])%4)))
    print(f"    settlement success   : {d.get('success')}")
    print(f"    on-chain tx          : {d.get('transaction')}")
    print(f"    explorer             : https://sepolia.basescan.org/tx/{d.get('transaction')}")
print(f"\nContent served: {r2.text.strip()[:120]}")
print(f"Balances AFTER   : payer={bal(acct.address)}  recipient={bal(RECIP)}")
print("\n[OK] 402 -> pay -> 200" if r2.status_code==200 else f"[!] status {r2.status_code}")

# Phase A — reproduction evidence

Account <LAB_ACCOUNT_ID>, us-east-1. Test host `i-0455b629d52b4131f` at `10.212.2.221`
(spoke subnet, next to the case's `10.212.2.214`).

## Client side

```
$ curl -v --max-time 18 https://iam.amazonaws.com
* Host iam.amazonaws.com:443 was resolved.
* IPv4: 44.216.186.86
*   Trying 44.216.186.86:443...
* Connection timed out after 18002 milliseconds
curl: (28) Connection timed out after 18002 milliseconds
```

Resolves to `44.216.186.86` — the same endpoint IP recorded in the case — and hangs the full
18 s at `Trying ...:443`. The TLS handshake line is never reached.

## Firewall ALERT log — TCP/443 events for this flow

```
sid=10003 action=allowed  count=39    PROBE ip any packet   proto=TCP dp=443 sni=-  app_proto=-
sid=30000 action=blocked  count=244   Default drop          proto=TCP dp=443 sni=-  app_proto=-
```

Silent (never fired for TCP/443): `sid=10001`, `sid=10002` (tls.sni probes), `sid=20090`
(round-1 buggy allowlist), `sid=20091` (corrected allowlist alert), `sid=20000` (the pass).

## Reading

- `sid:10003` (`alert ip 10.212.0.0/16 any -> any any`) fires — packets from the spoke reach the
  engine. This kills the "traffic never arrives / routing is broken" hypothesis.
- `sid:30000` (`drop ip any any -> any any (flow:to_server;)`) fires and blocks, on the **SYN**.
  Every one of these events has **no `tls` object, no `sni`, no `app_proto`** — the drop happens at
  connection setup, before any application layer exists.
- The two `tls.sni` probes and the allowlist rules never fire even once, because the handshake never
  completes, so the Client Hello is never sent, so the `tls.sni` buffer is never built. The `pass`
  rule at priority 99 is structurally unreachable.

This is a byte-for-byte match to the customer's log signature: `proto TCP`, `dest_port 443`, default
drop, no TLS metadata.

The NTP noise (`proto=UDP dp=123`) is the instance's chrony sync; it shows the same pattern —
`sid:10003` allowed, `sid:30000` blocked — and is incidental.

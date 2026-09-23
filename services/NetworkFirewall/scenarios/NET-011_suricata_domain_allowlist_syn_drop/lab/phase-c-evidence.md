# Phase C — fix option 1: delete the catch-all, use the stateful default action

The priority-999 rule group was removed from the policy entirely, and the default actions set to:

```
statefulDefaultActions = ["aws:drop_established", "aws:alert_established"]
```

Policy rule-group references after the change: priorities `[10, 99, 100]` only. Firewall `IN_SYNC`.

## Client side

```
iam=302/0.025    (allowlisted — passes)
example=000/12s  (not allowlisted — dropped by the default action)
```

Same functional outcome as Phase B: allowlisted AWS endpoints pass, everything else is denied.

## Firewall ALERT log

```
sid=10001 action=allowed  sni=iam.amazonaws.com  app_proto=tls   PROBE sni homenet
sid=10002 action=allowed  sni=iam.amazonaws.com  app_proto=tls   PROBE sni hardcoded src
sid=20091 action=allowed  sni=iam.amazonaws.com  app_proto=tls   MATCH amazonaws sni
sid=10003 action=allowed  sni=-                  app_proto=-     PROBE ip any packet (SYN)
sid=4     action=blocked  sni=example.com        app_proto=tls   (default action: aws:alert_established)
sid=3     action=blocked  sni=-                  app_proto=ntp   (default action on UDP)
```

## Why this is the cleaner fix

- **The deny is still logged.** `sid:4` is the reserved signature id AWS Network Firewall emits for
  `aws:alert_established`. The `example.com` deny appears in the ALERT log **with the SNI present**,
  because the drop now happens after the handshake, at the TLS layer — not on the SYN. This satisfies
  the compliance requirement (every denied connection produces an auditable event) that the explicit
  catch-all rule existed to meet, without the explicit rule.
- **`drop_established` cannot kill the SYN.** By construction it only matches flows past the
  handshake, so the original outage is structurally impossible under this configuration.
- **No explicit rule pre-empts the default action.** In Phase B the explicit `sid:30000` still ran
  ahead of the default action, so any future change to `statefulDefaultActions` would have no effect —
  a maintenance trap. Here the default action is the single source of truth for "deny everything not
  explicitly allowed".

## Summary across the three phases

- **Phase A (broken):** `drop ip any any (flow:to_server;)` matches the SYN. Log shows `sid:30000`
  blocked with no TLS metadata; every allowlist and tls.sni rule silent. curl hangs.
- **Phase B (option 2):** add `established`. SYN survives, handshake completes, `sid:20091` matches
  with the SNI, allowlisted traffic passes. Works, but the explicit rule still shadows the default
  action.
- **Phase C (option 1, preferred):** remove the explicit rule, set
  `["aws:drop_established","aws:alert_established"]`. Allowlisted traffic passes, non-allowlisted is
  denied and logged (`sid:4`, SNI present). Clean contract, compliance requirement met.

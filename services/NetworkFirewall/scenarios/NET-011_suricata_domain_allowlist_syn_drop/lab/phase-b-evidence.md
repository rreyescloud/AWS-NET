# Phase B — fix option 2: scope the catch-all drop to established flows

The priority-999 rule group was updated from:

```
drop ip any any -> any any (sid:30000;msg:"Default drop"; flow:to_server;)
```

to:

```
drop ip any any -> any any (msg:"Default drop"; flow:to_server,established; sid:30000; rev:1;)
```

Adding `established` is the only change. Firewall reached `IN_SYNC`.

## Client side

```
iam=302  time=0.025    (allowlisted — passes)
sts=302  time=0.025    (allowlisted — passes)
example=000 time=12s   (not allowlisted — dropped, connection hangs to timeout)
```

`iam.amazonaws.com` and `sts.amazonaws.com` now complete the handshake and return HTTP 302 in 25 ms.
`example.com` hangs to the full timeout — dropped, as intended.

## Firewall ALERT log — the flow now reaches the TLS layer

Immediately after the fix, the SYN-drop stops and TLS-layer events appear:

```
sid=10003 action=allowed  sni=-                  app_proto=-     PROBE ip any packet (SYN, control)
sid=10001 action=allowed  sni=iam.amazonaws.com  app_proto=tls   PROBE sni homenet
sid=10002 action=allowed  sni=iam.amazonaws.com  app_proto=tls   PROBE sni hardcoded src
sid=20091 action=allowed  sni=iam.amazonaws.com  app_proto=tls   MATCH amazonaws sni
```

## The contrast with Phase A, keyword by keyword

- **`sid:30000` no longer fires on the SYN.** In the previous 5-minute window under the fix, the only
  L3 event was `sid:10003`; `sid:30000` had vanished from the SYN. Adding `established` removed the
  match at connection setup — that is the entire fix.
- **`sid:10001` / `sid:10002` (tls.sni probes) now fire** with `sni=iam.amazonaws.com` and
  `app_proto=tls`. In Phase A they were silent, because the handshake never completed and the SNI
  buffer never existed. Their firing proves the buffer is now built.
- **`sid:20091` (corrected allowlist, as an alert) fires** with the SNI present. The rule the customer
  believed was "being ignored" matches perfectly once the SYN survives.
- **`sid:20090` (round-1 rule, `startswith`+`endswith` on one content) stays silent** even now that
  TLS works. This isolates the second, independent defect: the round-1 anchors were unsatisfiable, a
  separate bug from the SYN drop. A wildcard domain cannot equal the literal `.amazonaws.com`, which
  is what applying both anchors to a single `content` demands. `dotprefix` is the correct transform.

The TLS-layer events (`10001/10002/20091`) are delivered to CloudWatch a couple of minutes later than
the L3 `10003`/`30000` events, so allow for that lag when reading the log live.

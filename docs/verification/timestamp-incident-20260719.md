# Binance timestamp incident evidence — 2026-07-19 UTC

## Immutable run outcome

- Verification run `67d30648-ca59-429a-9203-c6c391f41d19` stopped at
  `2026-07-19T10:19:42.783272Z`.
- Stop reason: `binance_reconciliation_timestamp_sync_failed`.
- Attempts/trades/successes: `0/0/0`; starting and ending available USDT:
  `24.86306379`; Binance positions/orders after halt: `0/0`.
- The run is not resumed or modified by the remediation.

## Incident timeline and preserved log evidence

The deployed container log was captured from `10:16:30Z` through
`10:19:30Z` before source changes. It contained no key, signature, secret,
or signed URL. Relevant events were:

```text
10:17:14Z public market calls: ConnectTimeout after 11.7 seconds
10:17:32Z BTC scheduler_symbol_error: RemoteProtocolError
10:17:46Z /api/market/context latency: 22.011 seconds
10:17:56Z ETH scheduler_symbol_error: ReadTimeout
10:18:04Z binance_sync_failed: Timestamp for this request is outside of the recvWindow.
10:18:04Z prediction-resolution-summary latency: 63.345 seconds
10:18:52Z binance_sync_failed: Timestamp for this request is outside of the recvWindow.
```

The old reconciliation logger intentionally retained only a safe message;
it did not retain HTTP status, Binance numeric code, response payload, path,
RTT, or cached offset. Consequently those historical fields cannot be
reconstructed without fabrication. In the old client, this message reached
`binance_sync_failed` as `BinanceTimestampError`, which
`map_binance_error()` constructs only for Binance numeric code `-1021`.
The remediation logs future `-1021` events as safe fields: product, unsigned
path category, HTTP status, numeric Binance code, response message, and
refresh reason. Request query strings, signatures, headers, and credentials
remain excluded.

## Host and network measurements

Measured at `2026-07-19T11:34:04Z`:

- Host timezone: `Etc/UTC`; RTC in UTC.
- `timedatectl`: system clock synchronized `yes`, NTP service active.
- chrony active; reference `metadata.google.internal`; stratum 3; leap
  status Normal; system time `0.000005612` seconds fast; RMS offset
  `0.000003032` seconds.
- Container and host epoch observations differed only by command-launch
  latency; containers use the host kernel clock.
- Five direct USD-M `/fapi/v1/time` samples: 310–333 ms total RTT.
- Five direct Spot `/api/v3/time` samples: 304–341 ms total RTT.
- New midpoint estimator, five valid samples each:
  - USD-M Futures: offset `+20.848 ms`, median RTT `300.357 ms`.
  - Spot: offset `+13.182 ms`, median RTT `284.507 ms`.
- A signed read-only production reconciliation using the remediated source:
  USD-M offset `+14.776 ms`, RTT `283.187 ms`; Spot offset `+13.105 ms`,
  RTT `283.619 ms`; positions/orders `0/0`; balance `24.86306379 USDT`.

## Proven root cause

VM/NTP drift, timezone conversion, seconds-versus-milliseconds conversion,
and container clock divergence are ruled out by the measurements above and
by the old code's explicit millisecond epoch generation.

The failure was caused by divergent application timestamp implementations
under an acute network/request-latency episode:

1. `BinanceFuturesClient` kept a mutable offset per client instance.
2. `BinanceAdapter` signed independently with raw local wall time and never
   synchronized.
3. Spot permission requests reused the Futures client's offset despite a
   different product host.
4. The Futures refresh calculated `serverTime - localTimeAfterResponse`,
   ignoring request RTT and monotonic midpoint. A delayed response therefore
   biased the newly cached offset backward by the response delay.
5. The fixed receive window was 5,000 ms while contemporaneous application
   requests took 12–63 seconds. A delayed refresh could thus be invalid as
   soon as it was stored.
6. There was no offset age/health state, periodic refresh, sample rejection,
   robust multi-sample estimate, or pre-entry unsafe-clock gate.

The receive window remains 5,000 ms. The remediation fixes synchronization
rather than concealing the defect with a 60,000 ms window.

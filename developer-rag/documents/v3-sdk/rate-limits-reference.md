# Rate limits reference (SDK v3)

This page documents the rate-limit helpers in OctoKit SDK v3. The SDK enforces
the same primary rate limits as the REST API. The primary rate limit for
unauthenticated requests is 60 requests per hour. Authenticated requests using
a personal access token are limited to 5,000 requests per hour. Search
endpoints and a few other endpoints have more restrictive, secondary limits.

## getRateLimit()

`client.getRateLimit()` reports the current rate limit status for the
authenticated identity.

| Parameter | Type | Default | Required |
|---|---|---|---|
| request | object | {} | no |

## checkRateLimit()

`checkRateLimit()` returns `true` when the caller still has requests remaining
under the primary rate limit, and `false` otherwise.

| Parameter | Type | Default | Required |
|---|---|---|---|
| request | object | {} | no |

## waitForRateLimit()

`waitForRateLimit()` suspends execution until the current rate-limit window
resets, then returns the reset timestamp.

| Parameter | Type | Default | Required |
|---|---|---|---|
| poll_interval_ms | number | 5000 | no |

```js
await client.waitForRateLimit({ poll_interval_ms: 1000 });
console.log("window reset, resuming requests");
```
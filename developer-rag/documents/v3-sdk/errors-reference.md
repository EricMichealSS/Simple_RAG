# Errors reference (SDK v3)

This page documents the error classes that OctoKit SDK v3 throws. Every error
extends `ApiError` and carries a `status` property with the HTTP status code.
Errors are never swallowed; if a request fails the SDK always rejects with a
typed error.

## ApiError

The base class for every SDK error.

| Property | Type | Default | Required |
|---|---|---|---|
| message | string | — | yes |
| status | number | — | yes |
| headers | object | — | no |
| cause | Error | null | no |

## RateLimitError

Thrown when the client exhausts its rate limit and a request is rejected with
HTTP 429. It is raised after the built-in retries are exhausted.

| Property | Type | Default | Required |
|---|---|---|---|
| message | string | — | yes |
| status | number | 429 | yes |
| reset_at | string | — | no |
| retry_after_seconds | number | — | no |

```js
try {
  await client.send("GET /repos/{owner}/{repo}");
} catch (error) {
  if (error instanceof RateLimitError) {
    console.log("slow down until", error.reset_at);
  }
}
```

## ValidationError

Thrown when a request fails validation, typically with HTTP 422.

| Property | Type | Default | Required |
|---|---|---|---|
| message | string | — | yes |
| status | number | 422 | yes |
| errors | object[] | [] | no |

## AuthenticationError

Thrown when a request is rejected because the token is missing, expired or
insufficient for the endpoint.

| Property | Type | Default | Required |
|---|---|---|---|
| message | string | — | yes |
| status | number | 401 | yes |

```js
const auth = await createOAuthClient({ token: "invalid" });
auth.send("GET /user").catch((error) => {
  console.log(error instanceof AuthenticationError); // true
});
```
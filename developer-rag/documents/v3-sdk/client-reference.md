# OctoClient reference (SDK v3)

The OctoClient is the core entry point of the OctoKit SDK v3. Every SDK call
goes through an OctoClient instance. You construct one client per
authentication identity and reuse it for the lifetime of your process.

## OctoClient constructor

`new OctoClient(options)` creates a new client. All options are optional; a
client created with no options makes unauthenticated requests.

| Parameter | Type | Default | Required |
|---|---|---|---|
| auth | string \| object | none | no |
| base_url | string | https://api.github.com | no |
| user_agent | string | octokit.js/v3.0.0 | no |
| request_timeout_ms | number | 10000 | no |

```js
const client = new OctoClient({
  auth: process.env.GITHUB_TOKEN,
  user_agent: "my-app/1.0"
});
```

## Client.send()

`client.send()` is the low-level method that executes a single HTTP request
against the REST API. Every higher-level SDK method is built on top of
`send()`. It returns a Promise that resolves to the response body.

| Parameter | Type | Default | Required |
|---|---|---|---|
| endpoint | string | — | yes |
| method | string | GET | no |
| data | object | — | no |
| headers | object | — | no |
| retry_backoff_ms | number | 1000 | no |
| max_retries | number | 3 | no |
| timeout_ms | number | 10000 | no |
| enable_retries | boolean | true | no |
| follow_redirects | boolean | true | no |

`retry_backoff_ms` is the base delay between retry attempts, in milliseconds.
The SDK doubles this value after every failed attempt. `max_retries` caps the
total number of retry attempts per request. Set `enable_retries` to `false` to
disable the retry behaviour entirely.

```js
const body = await client.send("GET /repos/{owner}/{repo}", {
  retry_backoff_ms: 2000,
  max_retries: 5,
  timeout_ms: 30000
});
```

## Client.paginate()

`client.paginate()` iterates over every page of a paginated endpoint and
returns the concatenated results.

| Parameter | Type | Default | Required |
|---|---|---|---|
| route | string | — | yes |
| parameters | object | {} | no |
| map_fn | function | identity | no |

```js
const issues = await client.paginate("GET /issues", {
  state: "open"
});
```

## Client.getRateLimit()

`client.getRateLimit()` returns the rate limit status for the authenticated
user, including the remaining requests and the reset time.

| Parameter | Type | Default | Required |
|---|---|---|---|
| request | object | {} | no |

```js
const { data } = await client.getRateLimit();
console.log(data.rate.remaining, data.rate.reset);
```
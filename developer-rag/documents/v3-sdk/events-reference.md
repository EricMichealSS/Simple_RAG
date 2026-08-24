# Events and webhooks reference (SDK v3)

This page documents the webhook helpers in OctoKit SDK v3. Webhooks let your
application receive events such as `issues`, `push` and `pull_request` as
they happen, without polling.

## createWebhook()

`createWebhook()` registers a new webhook on a repository or organization.

| Parameter | Type | Default | Required |
|---|---|---|---|
| owner | string | — | yes |
| repo | string | — | yes |
| url | string | — | yes |
| events | string[] | ["push"] | no |
| secret | string | — | no |
| active | boolean | true | no |

```js
await client.createWebhook({
  owner: "octo",
  repo: "hello-world",
  url: "https://example.com/hook",
  events: ["issues", "pull_request"]
});
```

## listWebhooks()

`listWebhooks()` returns the webhooks registered on a repository, in pages.

| Parameter | Type | Default | Required |
|---|---|---|---|
| owner | string | — | yes |
| repo | string | — | yes |
| per_page | number | 30 | no |
| page | number | 1 | no |

## deleteWebhook()

`deleteWebhook()` removes a webhook by id.

| Parameter | Type | Default | Required |
|---|---|---|---|
| owner | string | — | yes |
| repo | string | — | yes |
| hook_id | number | — | yes |
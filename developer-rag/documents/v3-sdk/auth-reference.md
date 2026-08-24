# Authentication reference (SDK v3)

This page documents the authentication helpers shipped with OctoKit SDK v3.
The SDK supports personal access tokens, OAuth apps and GitHub Apps. Every
token-based helper in this page accepts a `token` and a set of scopes, and
returns an authenticated OctoClient.

## createOAuthClient()

`createOAuthClient(options)` builds a client authenticated with a token
obtained from an OAuth flow.

| Parameter | Type | Default | Required |
|---|---|---|---|
| client_id | string | — | yes |
| client_secret | string | — | yes |
| token | string | — | yes |
| scopes | string[] | [] | no |
| redirect_uri | string | — | no |

```js
const auth = await createOAuthClient({
  client_id: "Iv1.abc",
  client_secret: process.env.CLIENT_SECRET,
  token: exchangeCodeForToken(code)
});
```

## getAccessToken()

`getAccessToken()` exchanges an OAuth authorization code for an access token.

| Parameter | Type | Default | Required |
|---|---|---|---|
| code | string | — | yes |
| state | string | — | no |
| scopes | string[] | repo, workflow | no |

The default scope set of `repo` and `workflow` gives the token full access to
private repositories and to GitHub Actions workflow files. If you only need
read access to public data, pass a narrower scope list.

```js
const { token, expires_in } = await getAccessToken({
  code: authCode
});
```

## refreshToken()

`refreshToken()` rotates an expiring access token issued by an OAuth app.

| Parameter | Type | Default | Required |
|---|---|---|---|
| refresh_token | string | — | yes |
| grant_type | string | refresh_token | no |

```js
const { token } = await refreshToken({
  refresh_token: storedRefreshToken
});
```
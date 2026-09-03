# Uploads reference (SDK v3)

This page documents the upload helpers in OctoKit SDK v3. Uploads send binary
content such as release assets and issue attachments to the API.

## uploadReleaseAsset()

`uploadReleaseAsset()` uploads a binary asset to an existing release.

| Parameter | Type | Default | Required |
|---|---|---|---|
| owner | string | — | yes |
| repo | string | — | yes |
| release_id | number | — | yes |
| asset_name | string | — | yes |
| content_type | string | application/octet-stream | no |
| body | Buffer \| Stream | — | yes |

```js
await client.uploadReleaseAsset({
  owner: "octo",
  repo: "hello-world",
  release_id: 1,
  asset_name: "bundle.tar.gz",
  body: fs.createReadStream("bundle.tar.gz")
});
```

## uploadAttachment()

`uploadAttachment()` attaches a file to an issue comment.

| Parameter | Type | Default | Required |
|---|---|---|---|
| owner | string | — | yes |
| repo | string | — | yes |
| comment_id | number | — | yes |
| file | Buffer \| Stream | — | yes |
| name | string | — | no |
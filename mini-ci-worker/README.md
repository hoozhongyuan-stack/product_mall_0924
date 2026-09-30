# Isolated mini-program upload adapter

`miniprogram-ci@2.1.47` runs only in the Node container built from
`deploy/Dockerfile.mini-ci-node`. Django dispatches one task at a time to the
internal `POST /upload` endpoint on port 8787 using a dedicated Bearer token.
The endpoint is not published to the host. It accepts at most 32 KiB of JSON,
returns at most 4 KiB of structured status, and kills an SDK child process after
170 seconds. `GET /healthz` reports readiness without exposing task state.

The Python worker owns the database, encrypted configuration and immutable
package. It verifies the package, writes a temporary project copy into the
dedicated `ci-staging` tmpfs volume, and sends that project's path and the
current task's upload key to Node. Node mounts the volume read-only and has no
database, media, Django secret or WeChat encryption-key mount. The temporary
project is removed after the call. Each Node subprocess also gets a unique
mode-0700 directory under `/tmp` as its working directory, `HOME`, and `TMPDIR`.
The official SDK writes compiler caches there rather than beside the read-only
source or application. The process group is stopped and the directory is
removed after success, failure, timeout, and spawn failure. The upload key is
sent in the internal request body and is not written to the shared volume or logs.

For the full third-party flow, the project config uses the platform's
developer mini-program AppID and the temporary `ext.json` contains
`extEnable: true`, `extAppid: <target AppID>` and `directCommit: true`.
`CI_DIRECT` remains a separate development-version upload and cannot be used
as proof of eligibility for third-party review. The Node adapter does not
submit review or release. A timeout or malformed SDK response is
`UPLOAD_UNKNOWN`; Django never automatically retries it.

The bounded response keeps `ok` and `code` and, for failures, can include:

- `failureStage`: `ENVIRONMENT`, `VALIDATION`, `COMPILE`, `UPLOAD`, or `RESPONSE`.
- `sdkCode`: at most 48 safe letters, digits, `_`, `+`, `.`, and `-`.
- `platformErrorCode`: nonzero signed integer with absolute value at most 99,999,999.

Known local failures use `ENVIRONMENT_FAILED`, `COMPILE_FAILED`, or
`SIGNATURE_FAILED`; explicit nonzero WeChat responses use `WECHAT_REJECTED`.
SDK code `20003` alone only means the SDK wrapped an upload exception and
remains unknown. Official 2.1.47 wraps some backend JSON responses in its
exception message; only a bounded, complete recognized response envelope is
parsed for its numeric code. Raw messages, URLs, stack traces, response bodies,
and credentials are never returned. Existing validation codes remain supported.

## Offline compiler check

`npm test` verifies the adapter and error contract. On 2026-09-30 its 23 tests
pass, with 88.74% line and 82.61% branch coverage from Node's built-in coverage.
For a real SDK compiler check, run from this directory with an image built from
`deploy/Dockerfile.mini-ci-node` (replace the image tag as needed):

```sh
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 256 \
  --tmpfs /tmp:rw,nosuid,nodev,size=512m,uid=10001,gid=10001,mode=1777 \
  --mount "type=bind,src=$PWD/server.js,dst=/opt/mini-ci-worker/server.js,readonly" \
  --mount "type=bind,src=$PWD/upload.js,dst=/opt/mini-ci-worker/upload.js,readonly" \
  --mount "type=bind,src=$PWD/tests,dst=/opt/mini-ci-worker/tests,readonly" \
  product-mall-mini-ci:local-check node tests/sdk-offline-smoke.cjs
```

The check passed using official SDK 2.1.47 in a container running as UID 10001
with a read-only root and read-only fixture. It compiled JS/WXML/WXSS, verified
source writes fail with `EROFS`, and verified the private work directory was
removed. This fixture uses synthetic project attributes, blocks SDK HTTP calls,
and invokes the compiler only, never `ci.upload`. No real credentials are
mounted. It proves the writable-cache fix, not WeChat upload acceptance.

## Dependency status

The frozen dependency graph uses the [official miniprogram-ci distribution](https://github.com/wechat-miniprogram/miniprogram-ci-dist).
Overrides for `@babel/traverse@7.28.5`, `fast-xml-parser@4.5.5`,
`form-data@2.5.6`, and `protobufjs@7.5.6` are recorded in
`pnpm-workspace.yaml`. The `protobufjs` override crosses the SDK's declared
major-version range; unit tests and a module/protobuf round-trip smoke check
pass. Offline real-SDK fixture compilation is verified above; real WeChat
upload acceptance still needs a separate platform result.
Version 2.1.47 includes upstream Babel and COS fixes. On 2026-09-29,
`pnpm audit --prod` reports **0 critical, 12 high, 15 moderate and 2 low**
advisories. The audit also reports
`decompress@4.2.1` at a lower severity; no `decompress@4.2.2` is published.
The upload tests mock the SDK; a Docker build and module-import smoke test do
not prove a real WeChat upload. The Compose upload profile is disabled by
default pending a real-account UAT and a separate production security decision.

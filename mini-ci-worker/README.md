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
project is removed after the call. The upload key is sent in the internal
request body and is not written to the shared volume or logs.

For the full third-party flow, the project config uses the platform's
developer mini-program AppID and the temporary `ext.json` contains
`extEnable: true`, `extAppid: <target AppID>` and `directCommit: true`.
`CI_DIRECT` remains a separate development-version upload and cannot be used
as proof of eligibility for third-party review. The Node adapter does not
submit review or release. A timeout or malformed SDK response is
`UPLOAD_UNKNOWN`; Django never automatically retries it.

## Dependency status

The frozen dependency graph uses the [official miniprogram-ci distribution](https://github.com/wechat-miniprogram/miniprogram-ci-dist).
Overrides for `@babel/traverse@7.28.5`, `fast-xml-parser@4.5.5`,
`form-data@2.5.6`, and `protobufjs@7.5.6` are recorded in
`pnpm-workspace.yaml`. The `protobufjs` override crosses the SDK's declared
major-version range; unit tests and a module/protobuf round-trip smoke check
pass, but real SDK compilation and WeChat upload have not been verified.
Version 2.1.47 includes upstream Babel and COS fixes. On 2026-09-29,
`pnpm audit --prod` reports **0 critical, 12 high, 15 moderate and 2 low**
advisories. The audit also reports
`decompress@4.2.1` at a lower severity; no `decompress@4.2.2` is published.
The upload tests mock the SDK; a Docker build and module-import smoke test do
not prove a real WeChat upload. The Compose upload profile is disabled by
default pending a real-account UAT and a separate production security decision.

"""Materialize a fixed Git commit's Mini Program blobs for E3 deployment."""

import os
import json
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .package import IGNORED_DIRS, ROOT_FILES, SOURCE_DIRS, PackageError, build_package


REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
APP_ID = re.compile(r"wx[0-9a-fA-F]{16}\Z")
API_ORIGIN = re.compile(r"https://[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}\Z")
API_LITERAL = re.compile(r"(apiBaseUrl\s*:\s*)(['\"])([^'\"]+)\2")
HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")


def _valid_api_origin(value):
    if not isinstance(value, str) or not API_ORIGIN.fullmatch(value):
        return False
    parsed = urlsplit(value)
    host = parsed.hostname or ''
    labels = host.split('.')
    return (parsed.scheme == 'https' and parsed.netloc == host and
            not host.endswith(('.local', '.localhost', '.test', '.invalid')) and
            all(HOST_LABEL.fullmatch(label) for label in labels))


def _apply_release_config(root, app_id, api_base_url):
    if not APP_ID.fullmatch(app_id or '') or not _valid_api_origin(api_base_url):
        raise PackageError('RELEASE_CONFIG_INVALID')
    try:
        config_path = root / 'project.config.json'
        config = json.loads(config_path.read_text(encoding='utf-8'))
        if not isinstance(config, dict):
            raise ValueError()
        script_path = root / 'app.js'
        script = script_path.read_text(encoding='utf-8')
        if len(API_LITERAL.findall(script)) != 1:
            raise ValueError()
        config_path.write_text(json.dumps({**config, 'appid': app_id}, ensure_ascii=False,
                                          separators=(',', ':')) + '\n', encoding='utf-8')
        script_path.write_text(API_LITERAL.sub(lambda match: match.group(1) +
                               json.dumps(api_base_url), script), encoding='utf-8')
    except (OSError, ValueError, UnicodeError) as exc:
        raise PackageError('RELEASE_CONFIG_INVALID') from exc


def _git(repo, *args):
    try:
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("GIT_")}
        environment["GIT_NO_REPLACE_OBJECTS"] = "1"
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True, timeout=30, env=environment).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise PackageError("SOURCE_REVISION_INVALID") from exc


def _verify_checkout(repo, revision):
    if not isinstance(revision, str) or not REVISION.fullmatch(revision):
        raise PackageError("SOURCE_REVISION_INVALID")
    try:
        actual_root = Path(_git(repo, "rev-parse", "--show-toplevel").decode("utf-8").strip()).resolve()
        head = _git(repo, "rev-parse", "--verify", "HEAD^{commit}").decode("ascii").strip()
    except (UnicodeError, ValueError) as exc:
        raise PackageError("SOURCE_REVISION_INVALID") from exc
    if actual_root != repo or head != revision:
        raise PackageError("SOURCE_REVISION_INVALID")
    if _git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise PackageError("SOURCE_DIRTY")


def _entries(repo, revision):
    output = _git(repo, "ls-tree", "-r", "-z", "--full-tree", revision, "--", "mini-program")
    if not output:
        raise PackageError("SOURCE_UNAVAILABLE")
    for raw in output.split(b"\0"):
        if not raw:
            continue
        try:
            metadata, name = raw.split(b"\t", 1)
            mode, kind, object_id = metadata.decode("ascii").split(" ")
            relative = name.decode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise PackageError("SOURCE_INVALID") from exc
        parts = relative.split("/")
        if (parts[0] != "mini-program" or len(parts) < 2 or
                any(not part or part in {".", ".."} or "\\" in part or
                    any(ord(char) < 32 for char in part) for part in parts)):
            raise PackageError("SOURCE_INVALID")
        source_parts = parts[1:]
        if len(source_parts) == 1:
            if source_parts[0] not in ROOT_FILES and source_parts[0] not in SOURCE_DIRS:
                continue
        elif (source_parts[0] not in SOURCE_DIRS or
              any(part in IGNORED_DIRS or part.startswith(".")
                  for part in source_parts[1:-1]) or
              source_parts[-1].startswith(".") or
              source_parts[-1] in {"project.private.config.json", "package-lock.json"}):
            continue
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise PackageError("SOURCE_INVALID")
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", object_id):
            raise PackageError("SOURCE_INVALID")
        yield parts, object_id


def build_git_package(repo_root, expected_revision, maximum_bytes, *, release_app_id=None,
                      api_base_url=None):
    """Package Git blobs only when a clean checkout is pinned to full HEAD."""
    if not isinstance(maximum_bytes, int) or maximum_bytes <= 0:
        raise PackageError("PACKAGE_TOO_LARGE")
    repo = Path(repo_root).resolve()
    _verify_checkout(repo, expected_revision)
    total = 0
    # Keep staging outside the checkout: an interrupted process must not leave
    # an untracked tree that makes every later deployment appear dirty.
    try:
        staging_root = Path(tempfile.gettempdir()).resolve()
        if staging_root == repo or repo in staging_root.parents:
            raise PackageError("SOURCE_UNAVAILABLE")
        with tempfile.TemporaryDirectory(prefix="mini-code-git-", dir=staging_root) as temporary:
            destination = Path(temporary)
            for parts, object_id in _entries(repo, expected_revision):
                try:
                    size = int(_git(repo, "cat-file", "-s", object_id).decode("ascii").strip())
                except (ValueError, UnicodeError) as exc:
                    raise PackageError("SOURCE_INVALID") from exc
                if size < 0:
                    raise PackageError("SOURCE_INVALID")
                total += size
                if total > maximum_bytes:
                    raise PackageError("PACKAGE_TOO_LARGE")
                content = _git(repo, "cat-file", "blob", object_id)
                if len(content) != size:
                    raise PackageError("SOURCE_CHANGED")
                target = destination.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            if release_app_id is not None or api_base_url is not None:
                _apply_release_config(destination / 'mini-program', release_app_id, api_base_url)
            package = build_package(destination / "mini-program", maximum_bytes)
    except OSError as exc:
        raise PackageError("SOURCE_UNAVAILABLE") from exc
    _verify_checkout(repo, expected_revision)
    return package

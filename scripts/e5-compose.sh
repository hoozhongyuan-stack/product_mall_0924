#!/bin/sh
# Read only this literal enum; never source an environment file as shell code.
if [ -n "${E5_ENV_FILE:-}" ]; then
    e5_file_profile=$(awk '
        /^[[:space:]]*(export[[:space:]]+)?E5_DEPLOY_PROFILE([[:space:]]|=|$)/ {
            count++
            if ($0 !~ /^[[:space:]]*(export[[:space:]]+)?E5_DEPLOY_PROFILE[[:space:]]*=/) { bad=1; next }
            value=$0; sub(/^[^=]*=/,"",value)
            gsub(/^[[:space:]]+|[[:space:]]+$/,"",value)
            if (value != "production" && value != "uat") bad=1
        }
        END { if (bad || count > 1) exit 1; print count ? value : "production" }
    ' "$E5_ENV_FILE") || {
        echo 'Invalid or duplicate E5_DEPLOY_PROFILE in the environment file.' >&2
        exit 2
    }
    if [ "${E5_DEPLOY_PROFILE+x}" = x ] && [ "$E5_DEPLOY_PROFILE" != "$e5_file_profile" ]; then
        echo 'Shell and environment-file deployment profiles must agree.' >&2
        exit 2
    fi
    E5_DEPLOY_PROFILE=$e5_file_profile
    export E5_DEPLOY_PROFILE
    unset e5_file_profile
fi
case "${E5_DEPLOY_PROFILE:-production}" in
    production|uat) ;;
    *) echo 'E5_DEPLOY_PROFILE must be production or uat.' >&2; exit 2 ;;
esac
case "${E5_IMAGE_MODE:-build}" in
    build|prebuilt) ;;
    *) echo 'E5_IMAGE_MODE must be build or prebuilt.' >&2; exit 2 ;;
esac
case "${E5_CODE_RELEASE_ENABLED:-0}" in
    0|1) ;;
    *) echo 'E5_CODE_RELEASE_ENABLED must be 0 or 1.' >&2; exit 2 ;;
esac
e5_code_release_services=''
if [ "${E5_CODE_RELEASE_ENABLED:-0}" = 1 ]; then
    e5_code_release_services='mini-ci-adapter code-upload-worker'
    : "${E5_MINI_CI_DISPATCH_TOKEN_FILE:?Set the private CI dispatch token file}"
    test -r "$E5_MINI_CI_DISPATCH_TOKEN_FILE" || {
        echo 'CI dispatch token file is unreadable.' >&2
        exit 2
    }
    python3 -c 'import os,re,stat,sys; p=sys.argv[1]; s=os.stat(p); value=open(p,"rb").read(256).strip(); sys.exit(0 if stat.S_ISREG(s.st_mode) and not (s.st_mode & 0o077) and re.fullmatch(rb"[A-Za-z0-9_-]{32,128}",value) else 1)' "$E5_MINI_CI_DISPATCH_TOKEN_FILE" || {
        echo 'CI dispatch token must be a private regular file with a 32-128 character URL-safe token.' >&2
        exit 2
    }
fi

compose() {
    if [ "${E5_CODE_RELEASE_ENABLED:-0}" = 1 ]; then
        set -- --profile code-release "$@"
    fi
    if [ "${E5_ISOLATED_RECOVERY:-0}" = 1 ]; then
        set -- -f "$root/compose.recovery.yaml" "$@"
    elif [ "${E5_DEPLOY_PROFILE:-production}" = uat ]; then
        set -- -f "$root/compose.uat-edge.yaml" "$@"
    fi
    if [ "${E5_DEPLOY_PROFILE:-production}" = uat ]; then
        set -- -f "$root/compose.production.yaml" -f "$root/compose.uat.yaml" "$@"
    else
        set -- -f "$root/compose.production.yaml" "$@"
    fi
    if [ -n "${E5_ENV_FILE:-}" ]; then
        set -- --env-file "$E5_ENV_FILE" "$@"
    fi
    docker compose "$@"
}

e5_prepare_images() {
    if [ "${E5_IMAGE_MODE:-build}" = build ]; then
        "$1" build release admin $e5_code_release_services
        # backup declares the shared ops image build; restore only consumes it.
        "$1" --profile ops build backup
        return
    fi
    # Missing images or a misleading tag must fail before any stop/up/migration.
    e5_images='backend admin ops'
    if [ "${E5_CODE_RELEASE_ENABLED:-0}" = 1 ]; then
        e5_images="$e5_images mini-ci"
    fi
    for e5_image in $e5_images; do
        e5_image_revision=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}|{{.Os}}/{{.Architecture}}' \
            "product-mall-$e5_image:$MALL_RELEASE_REVISION") || return 1
        if [ "$e5_image_revision" != "$MALL_RELEASE_REVISION|${E5_IMAGE_PLATFORM:-linux/amd64}" ]; then
            echo 'Prebuilt image does not match the exact release revision and target platform.' >&2
            return 1
        fi
    done
    unset e5_image e5_image_revision
}

e5_assert_new_project() {
    e5_project_containers=$(docker ps -aq --filter "label=com.docker.compose.project=$1") || {
        echo 'Cannot verify whether the target Compose project already exists.' >&2
        return 1
    }
    if [ -n "$e5_project_containers" ]; then
        echo 'Target Compose project already has running or stopped containers.' >&2
        return 1
    fi
    unset e5_project_containers
}

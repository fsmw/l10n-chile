#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target="${1:-both}"

case "$target" in
    odoo|ocb|both) ;;
    *)
        echo "Usage: $0 [odoo|ocb|both]" >&2
        exit 2
        ;;
esac

for generated_file in test-constraints.txt test-requirements.txt; do
    if [[ -e "$repo_root/$generated_file" ]]; then
        echo "$generated_file already exists; move it aside before running OCA CI." >&2
        exit 1
    fi
done

if ! command -v pre-commit >/dev/null 2>&1; then
    echo "pre-commit is required. Install it with: python -m pip install pre-commit" >&2
    exit 1
fi

(cd "$repo_root" && SKIP=oca-gen-addon-readme pre-commit run --all-files \
    --show-diff-on-failure --color=always)

compose=(docker compose -p l10n-chile-oca-ci -f "$repo_root/docker-compose.oca-ci.yml")
cleanup() {
    "${compose[@]}" down --remove-orphans
    rm -f "$repo_root/test-constraints.txt" "$repo_root/test-requirements.txt"
}
trap cleanup EXIT

for engine in odoo ocb; do
    if [[ "$target" != both && "$target" != "$engine" ]]; then
        continue
    fi
    "${compose[@]}" --profile "$engine" run --rm "test-$engine"
    rm -f "$repo_root/test-constraints.txt" "$repo_root/test-requirements.txt"
done

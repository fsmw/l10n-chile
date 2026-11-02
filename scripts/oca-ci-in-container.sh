#!/usr/bin/env bash
set -euo pipefail

for reqfile in requirements.txt test-requirements.txt; do
    if [[ -f "$reqfile" ]] && grep -nE '^[^#].*/' "$reqfile"; then
        echo "Unreleased dependencies found in $reqfile." >&2
        exit 1
    fi
done

oca_install_addons
manifestoo -d . check-licenses
manifestoo -d . check-dev-status --default-dev-status=Beta
oca_init_test_database
oca_run_tests

#!/bin/sh
set -eu
mkdir -p /data/skills "$SKILLOS_GOVERNANCE_REPO"
if [ ! -d "$SKILLOS_GOVERNANCE_REPO/.git" ]; then
    git init "$SKILLOS_GOVERNANCE_REPO"
fi
exec "$@"

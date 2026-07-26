#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
export NPM_CONFIG_CACHE="${NPM_CONFIG_CACHE:-/tmp/npm-cache}"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/tmp/ms-playwright}"

npm ci
if [[ "${CI:-}" == "true" ]]; then
  npx playwright install --with-deps chromium
else
  npx playwright install chromium
fi
npm run verify

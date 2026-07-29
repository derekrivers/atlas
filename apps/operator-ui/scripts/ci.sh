#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
export NPM_CONFIG_CACHE="${NPM_CONFIG_CACHE:-/tmp/npm-cache}"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/tmp/ms-playwright}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"

npm ci
if [[ "${CI:-}" == "true" ]]; then
  ./node_modules/.bin/playwright install --with-deps chromium
else
  ./node_modules/.bin/playwright install chromium
fi
npm run api:check
npm run lint
npm run typecheck
npm run test:acceptance
npm run test:browser
npm run build:bundle

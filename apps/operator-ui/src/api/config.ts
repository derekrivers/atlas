const DEFAULT_ATLAS_API_BASE_URL = 'http://127.0.0.1:8000'

function withoutTrailingSlash(value: string): string {
  return value.replace(/\/+$/, '')
}

export const ATLAS_API_BASE_URL = withoutTrailingSlash(
  import.meta.env.VITE_ATLAS_API_BASE_URL ?? DEFAULT_ATLAS_API_BASE_URL
)

export const ATLAS_API_PROXY_PREFIX = '/api'

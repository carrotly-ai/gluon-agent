/**
 * Version information for the application.
 *
 * Build-time version is injected via Vite environment variables.
 * Runtime version is fetched from the /api/version endpoint.
 */

export interface VersionInfo {
  version: string // Short git commit SHA
  full_version: string // Full git commit SHA
  build_time: string // ISO timestamp of build
  environment: string // "development" or "production"
  semver: string // Semantic version (e.g. "0.11.0")
}

/**
 * Get the build-time version baked into the frontend at build time.
 * This represents the version of the currently loaded app.
 */
export function getBuildVersion(): VersionInfo {
  return {
    version: import.meta.env.VITE_APP_VERSION || 'dev',
    full_version: import.meta.env.VITE_APP_FULL_VERSION || 'development',
    build_time: import.meta.env.VITE_APP_BUILD_TIME || new Date().toISOString(),
    environment: import.meta.env.VITE_APP_VERSION ? 'production' : 'development',
    semver: import.meta.env.VITE_APP_SEMVER || '',
  }
}

/**
 * Fetch the current server version from the API.
 * This represents what version is currently deployed.
 */
export async function fetchServerVersion(): Promise<VersionInfo> {
  const response = await fetch('/api/version')
  if (!response.ok) {
    throw new Error(`Failed to fetch version: ${response.status}`)
  }
  return response.json()
}

/**
 * Check if there's a newer version available.
 * Compares the build-time version with the server version.
 */
export function isNewerVersionAvailable(
  buildVersion: VersionInfo,
  serverVersion: VersionInfo
): boolean {
  // In development mode, skip version checking
  if (buildVersion.environment === 'development') {
    return false
  }

  // Compare full version hashes - if different, there's an update
  return buildVersion.full_version !== serverVersion.full_version
}

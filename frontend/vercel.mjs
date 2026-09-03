const configuredOrigin = process.env.RENDER_API_ORIGIN?.trim()

if (!configuredOrigin) {
  throw new Error('RENDER_API_ORIGIN must be set to the deployed Render HTTPS origin')
}

let backendUrl
try {
  backendUrl = new URL(configuredOrigin)
} catch {
  throw new Error('RENDER_API_ORIGIN must be a valid absolute URL')
}

if (
  backendUrl.protocol !== 'https:' ||
  backendUrl.username ||
  backendUrl.password ||
  backendUrl.pathname !== '/' ||
  backendUrl.search ||
  backendUrl.hash
) {
  throw new Error('RENDER_API_ORIGIN must be an HTTPS origin without credentials, path, query, or hash')
}

const backendOrigin = backendUrl.origin

export const config = {
  framework: 'vite',
  installCommand: 'npm ci',
  buildCommand: 'npm run build',
  outputDirectory: 'dist',
  trailingSlash: false,
  rewrites: [
    { source: '/api/:path*', destination: `${backendOrigin}/api/:path*` },
    { source: '/health', destination: `${backendOrigin}/health` },
    { source: '/health/:path*', destination: `${backendOrigin}/health/:path*` },
    { source: '/(.*)', destination: '/index.html' },
  ],
  headers: [
    {
      source: '/(.*)',
      headers: [
        { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
        { key: 'X-Content-Type-Options', value: 'nosniff' },
        { key: 'X-Frame-Options', value: 'DENY' },
        { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
      ],
    },
    {
      source: '/assets/(.*)',
      headers: [{ key: 'Cache-Control', value: 'public, max-age=31536000, immutable' }],
    },
  ],
}

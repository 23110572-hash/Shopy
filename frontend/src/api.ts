import type {
  AccountProfile,
  AgentChatRequest,
  AgentChatResponse,
  AgentControls,
  AgentControlsUpdate,
  AuthResponse,
  CatalogPage,
  HealthStatus,
  LoginRequest,
  OrderHistoryResponse,
  ProductCategory,
  SignupRequest,
  TransactionHistoryResponse,
} from './types'

const configuredBaseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim()
const developmentDefault = `${window.location.protocol}//${window.location.hostname}:8000`
const runtimeDefault = import.meta.env.DEV ? developmentDefault : ''
export const API_BASE_URL = (configuredBaseUrl || runtimeDefault).replace(/\/$/, '')

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function readCookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`
  const item = document.cookie.split('; ').find((cookie) => cookie.startsWith(prefix))
  return item ? decodeURIComponent(item.slice(prefix.length)) : null
}

async function requestJson<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  headers.set('Accept', 'application/json')
  if (options.body !== undefined) headers.set('Content-Type', 'application/json')

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
    credentials: 'include',
  })
  if (!response.ok) {
    let detail = `API request failed with status ${response.status}`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
      else if (Array.isArray(body.detail) && body.detail.length > 0) {
        const first = body.detail[0] as { msg?: unknown }
        if (typeof first.msg === 'string') detail = first.msg
      }
    } catch {
      // Keep the status-only message when the response is not JSON.
    }
    throw new ApiError(response.status, detail)
  }
  return (await response.json()) as T
}

function protectedHeaders(): HeadersInit {
  const csrfToken = readCookie('shopy_csrf')
  return csrfToken ? { 'X-CSRF-Token': csrfToken } : {}
}

export function fetchHealth(signal?: AbortSignal): Promise<HealthStatus> {
  return requestJson<HealthStatus>('/health', { signal })
}

export function fetchCatalog(
  query: string,
  category: ProductCategory | 'all',
  signal?: AbortSignal,
): Promise<CatalogPage> {
  const parameters = new URLSearchParams({ limit: '100' })
  if (query.trim()) parameters.set('q', query.trim())
  if (category !== 'all') parameters.set('category', category)
  return requestJson<CatalogPage>(`/api/catalog?${parameters.toString()}`, { signal })
}

export function sendAgentChat(
  request: AgentChatRequest,
  signal?: AbortSignal,
): Promise<AgentChatResponse> {
  return requestJson<AgentChatResponse>('/api/agent/chat', {
    method: 'POST',
    body: JSON.stringify(request),
    signal,
  })
}

export function fetchAccountProfile(signal?: AbortSignal): Promise<AccountProfile> {
  return requestJson<AccountProfile>('/api/account/profile', { signal })
}

export function signupAccount(payload: SignupRequest): Promise<AuthResponse> {
  return requestJson<AuthResponse>('/api/auth/signup', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function loginAccount(payload: LoginRequest): Promise<AuthResponse> {
  return requestJson<AuthResponse>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function logoutAccount(): Promise<{ message: string }> {
  return requestJson<{ message: string }>('/api/auth/logout', {
    method: 'POST',
    headers: protectedHeaders(),
  })
}

export function updateAccountProfile(displayName: string): Promise<AccountProfile> {
  return requestJson<AccountProfile>('/api/account/profile', {
    method: 'PATCH',
    headers: protectedHeaders(),
    body: JSON.stringify({ display_name: displayName }),
  })
}

export function fetchAgentControls(signal?: AbortSignal): Promise<AgentControls> {
  return requestJson<AgentControls>('/api/account/agent-controls', { signal })
}

export function updateAgentControls(payload: AgentControlsUpdate): Promise<AgentControls> {
  return requestJson<AgentControls>('/api/account/agent-controls', {
    method: 'PUT',
    headers: protectedHeaders(),
    body: JSON.stringify(payload),
  })
}

export function fetchOrderHistory(signal?: AbortSignal): Promise<OrderHistoryResponse> {
  return requestJson<OrderHistoryResponse>('/api/account/orders', { signal })
}

export function fetchTransactionHistory(
  signal?: AbortSignal,
): Promise<TransactionHistoryResponse> {
  return requestJson<TransactionHistoryResponse>('/api/account/transactions', { signal })
}

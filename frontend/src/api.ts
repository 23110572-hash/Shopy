import type {
  AccountProfile,
  AgentChatRequest,
  AgentChatResponse,
  AgentControls,
  AgentControlsUpdate,
  AuthResponse,
  CatalogPage,
  CheckoutCallback,
  CheckoutSession,
  CustomerOrder,
  DeliveryAddress,
  DeliveryAddressInput,
  DeliveryAddressList,
  HealthStatus,
  LoginRequest,
  OrderHistoryResponse,
  OrderListResponse,
  PlaceOrderRequest,
  PlaceOrderResponse,
  ProductCategory,
  PurchaseRunStatus,
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

/**
 * Derive a stable Idempotency-Key from the proposal so that retrying a failed
 * or interrupted attempt reuses the same Razorpay Order instead of creating a
 * duplicate one. Matches the backend charset (letters, digits, - _ . :) and the
 * required 16-128 character length.
 */
function proposalIdempotencyKey(proposalId: string): string {
  return `checkout:${proposalId}`
}

export function createCheckoutOrder(proposalId: string): Promise<CheckoutSession> {
  return requestJson<CheckoutSession>('/api/checkout/orders', {
    method: 'POST',
    headers: { ...protectedHeaders(), 'Idempotency-Key': proposalIdempotencyKey(proposalId) },
    body: JSON.stringify({ proposal_id: proposalId }),
  })
}

export function fetchCheckoutStatus(
  runId: string,
  signal?: AbortSignal,
): Promise<PurchaseRunStatus> {
  return requestJson<PurchaseRunStatus>(`/api/checkout/runs/${runId}`, { signal })
}

export function confirmCheckoutPayment(
  runId: string,
  callback: CheckoutCallback,
): Promise<PurchaseRunStatus> {
  return requestJson<PurchaseRunStatus>(`/api/checkout/runs/${runId}/confirm`, {
    method: 'POST',
    headers: protectedHeaders(),
    body: JSON.stringify(callback),
  })
}

export function reconcileCheckoutPayment(runId: string): Promise<PurchaseRunStatus> {
  return requestJson<PurchaseRunStatus>(`/api/checkout/runs/${runId}/reconcile`, {
    method: 'POST',
    headers: protectedHeaders(),
  })
}

/* ------------------------------------------------- delivery addresses & orders */

export function fetchAddresses(signal?: AbortSignal): Promise<DeliveryAddressList> {
  return requestJson<DeliveryAddressList>('/api/account/addresses', { signal })
}

export function createAddress(payload: DeliveryAddressInput): Promise<DeliveryAddress> {
  return requestJson<DeliveryAddress>('/api/account/addresses', {
    method: 'POST',
    headers: protectedHeaders(),
    body: JSON.stringify(payload),
  })
}

export function updateAddress(
  addressId: string,
  payload: DeliveryAddressInput,
): Promise<DeliveryAddress> {
  return requestJson<DeliveryAddress>(`/api/account/addresses/${addressId}`, {
    method: 'PUT',
    headers: protectedHeaders(),
    body: JSON.stringify(payload),
  })
}

export async function deleteAddress(addressId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/account/addresses/${addressId}`, {
    method: 'DELETE',
    headers: { Accept: 'application/json', ...protectedHeaders() },
    credentials: 'include',
  })
  if (!response.ok) {
    throw new ApiError(response.status, 'The address could not be removed.')
  }
}

export function placeOrder(payload: PlaceOrderRequest): Promise<PlaceOrderResponse> {
  return requestJson<PlaceOrderResponse>('/api/orders', {
    method: 'POST',
    headers: protectedHeaders(),
    body: JSON.stringify(payload),
  })
}

export function confirmOrderPayment(
  orderId: string,
  callback: CheckoutCallback,
): Promise<CustomerOrder> {
  return requestJson<CustomerOrder>(`/api/orders/${orderId}/confirm-payment`, {
    method: 'POST',
    headers: protectedHeaders(),
    body: JSON.stringify(callback),
  })
}

export function reconcileOrderPayment(orderId: string): Promise<CustomerOrder> {
  return requestJson<CustomerOrder>(`/api/orders/${orderId}/reconcile`, {
    method: 'POST',
    headers: protectedHeaders(),
  })
}

export function fetchOrders(signal?: AbortSignal): Promise<OrderListResponse> {
  return requestJson<OrderListResponse>('/api/orders', { signal })
}

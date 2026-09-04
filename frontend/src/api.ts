import type {
  AccountProfile, AgentChatRequest, AgentChatResponse, AgentConversationDetail,
  AgentConversationList, AgentConversationSummary, AgentControls, AgentControlsUpdate,
  AgentRunHistoryResponse, AuditHistoryResponse, AuthResponse, CatalogPage, CheckoutCallback,
  CheckoutSession, CustomerOrder, DeliveryAddress, DeliveryAddressInput, DeliveryAddressList,
  HealthStatus, LoginRequest, OrderHistoryResponse, OrderListResponse, PlaceOrderRequest,
  PlaceOrderResponse, ProductCategory, PurchaseRunStatus, SignupRequest,
  TransactionHistoryResponse,
} from './types'

const configuredBaseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim()
const developmentDefault = `${window.location.protocol}//${window.location.hostname}:8000`
const runtimeDefault = import.meta.env.DEV ? developmentDefault : ''
export const API_BASE_URL = (configuredBaseUrl || runtimeDefault).replace(/\/$/, '')

export class ApiError extends Error {
  readonly status: number
  readonly code: string | null
  readonly detail: unknown
  constructor(status: number, message: string, code: string | null = null, detail: unknown = null) {
    super(message); this.name = 'ApiError'; this.status = status; this.code = code; this.detail = detail
  }
}

function readCookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`
  const item = document.cookie.split('; ').find((cookie) => cookie.startsWith(prefix))
  return item ? decodeURIComponent(item.slice(prefix.length)) : null
}
function protectedHeaders(): HeadersInit {
  const token = readCookie('shopy_csrf')
  return token ? { 'X-CSRF-Token': token } : {}
}
async function requestJson<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers); headers.set('Accept', 'application/json')
  if (options.body !== undefined) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers, credentials: 'include' })
  if (!response.ok) {
    let message = `API request failed with status ${response.status}`; let code: string | null = null; let detail: unknown = null
    try {
      const body = await response.json() as { detail?: unknown }; detail = body.detail
      if (typeof body.detail === 'string') message = body.detail
      else if (body.detail && typeof body.detail === 'object' && !Array.isArray(body.detail)) {
        const structured = body.detail as { code?: unknown; message?: unknown }
        if (typeof structured.code === 'string') code = structured.code
        if (typeof structured.message === 'string') message = structured.message
      } else if (Array.isArray(body.detail) && body.detail.length) {
        const first = body.detail[0] as { msg?: unknown }; if (typeof first.msg === 'string') message = first.msg
      }
    } catch { /* preserve status message */ }
    throw new ApiError(response.status, message, code, detail)
  }
  if (response.status === 204) return undefined as T
  return await response.json() as T
}

export const fetchHealth = (signal?: AbortSignal) => requestJson<HealthStatus>('/health', { signal })
export function fetchCatalog(query: string, category: ProductCategory | 'all', signal?: AbortSignal) {
  const p = new URLSearchParams({ limit: '100' }); if (query.trim()) p.set('q', query.trim()); if (category !== 'all') p.set('category', category)
  return requestJson<CatalogPage>(`/api/catalog?${p}`, { signal })
}
export function sendAgentChat(payload: AgentChatRequest, signal?: AbortSignal) {
  return requestJson<AgentChatResponse>('/api/agent/chat', { method: 'POST', headers: protectedHeaders(), body: JSON.stringify(payload), signal })
}
export function createAgentConversation(title?: string) {
  return requestJson<AgentConversationSummary>('/api/agent/conversations', { method: 'POST', headers: protectedHeaders(), body: JSON.stringify({ title: title ?? null }) })
}
export async function fetchAgentConversations(signal?: AbortSignal): Promise<AgentConversationList> {
  const result = await requestJson<AgentConversationList>('/api/agent/conversations', { signal })
  return { items: result.items.filter((conversation) => conversation.status === 'ACTIVE') }
}
export const fetchAgentConversation = (id: string, signal?: AbortSignal) => requestJson<AgentConversationDetail>(`/api/agent/conversations/${id}`, { signal })
export const closeAgentConversation = (id: string) => requestJson<void>(`/api/agent/conversations/${id}`, { method: 'DELETE', headers: protectedHeaders() })
export async function clearAgentHistory(): Promise<void> {
  await requestJson<void>('/api/agent/conversations', {
    method: 'DELETE',
    headers: protectedHeaders(),
  })
}
export const fetchAgentRuns = (signal?: AbortSignal) => requestJson<AgentRunHistoryResponse>('/api/account/runs', { signal })
export const fetchRunAudit = (runId: string, signal?: AbortSignal) => requestJson<AuditHistoryResponse>(`/api/account/runs/${runId}/audit`, { signal })

export const fetchAccountProfile = (signal?: AbortSignal) => requestJson<AccountProfile>('/api/account/profile', { signal })
export const signupAccount = (payload: SignupRequest) => requestJson<AuthResponse>('/api/auth/signup', { method: 'POST', body: JSON.stringify(payload) })
export const loginAccount = (payload: LoginRequest) => requestJson<AuthResponse>('/api/auth/login', { method: 'POST', body: JSON.stringify(payload) })
export const logoutAccount = () => requestJson<{ message: string }>('/api/auth/logout', { method: 'POST', headers: protectedHeaders() })
export const updateAccountProfile = (display_name: string) => requestJson<AccountProfile>('/api/account/profile', { method: 'PATCH', headers: protectedHeaders(), body: JSON.stringify({ display_name }) })
export const fetchAgentControls = (signal?: AbortSignal) => requestJson<AgentControls>('/api/account/agent-controls', { signal })
export const updateAgentControls = (payload: AgentControlsUpdate) => requestJson<AgentControls>('/api/account/agent-controls', { method: 'PUT', headers: protectedHeaders(), body: JSON.stringify(payload) })
export const fetchOrderHistory = (signal?: AbortSignal) => requestJson<OrderHistoryResponse>('/api/account/orders', { signal })
export const fetchTransactionHistory = (signal?: AbortSignal) => requestJson<TransactionHistoryResponse>('/api/account/transactions', { signal })

const proposalKey = (proposalId: string) => `checkout:${proposalId}`
export function createCheckoutOrder(proposalId: string, addressId?: string) {
  if (!addressId) return Promise.reject(new ApiError(400, 'Confirm a saved delivery address in the Agent workspace before checkout.', 'ADDRESS_REQUIRED'))
  return requestJson<CheckoutSession>('/api/checkout/orders', { method: 'POST', headers: { ...protectedHeaders(), 'Idempotency-Key': proposalKey(proposalId) }, body: JSON.stringify({ proposal_id: proposalId, address_id: addressId }) })
}
export const fetchCheckoutStatus = (runId: string, signal?: AbortSignal) => requestJson<PurchaseRunStatus>(`/api/checkout/runs/${runId}`, { signal })
export const confirmCheckoutPayment = (runId: string, callback: CheckoutCallback) => requestJson<PurchaseRunStatus>(`/api/checkout/runs/${runId}/confirm`, { method: 'POST', headers: protectedHeaders(), body: JSON.stringify(callback) })
export const reconcileCheckoutPayment = (runId: string) => requestJson<PurchaseRunStatus>(`/api/checkout/runs/${runId}/reconcile`, { method: 'POST', headers: protectedHeaders() })

export const fetchAddresses = (signal?: AbortSignal) => requestJson<DeliveryAddressList>('/api/account/addresses', { signal })
export const createAddress = (payload: DeliveryAddressInput) => requestJson<DeliveryAddress>('/api/account/addresses', { method: 'POST', headers: protectedHeaders(), body: JSON.stringify(payload) })
export const updateAddress = (id: string, payload: DeliveryAddressInput) => requestJson<DeliveryAddress>(`/api/account/addresses/${id}`, { method: 'PUT', headers: protectedHeaders(), body: JSON.stringify(payload) })
export async function deleteAddress(id: string): Promise<void> { await requestJson<void>(`/api/account/addresses/${id}`, { method: 'DELETE', headers: protectedHeaders() }) }
export const placeOrder = (payload: PlaceOrderRequest) => requestJson<PlaceOrderResponse>('/api/orders', { method: 'POST', headers: protectedHeaders(), body: JSON.stringify(payload) })
export const confirmOrderPayment = (id: string, callback: CheckoutCallback) => requestJson<CustomerOrder>(`/api/orders/${id}/confirm-payment`, { method: 'POST', headers: protectedHeaders(), body: JSON.stringify(callback) })
export const reconcileOrderPayment = (id: string) => requestJson<CustomerOrder>(`/api/orders/${id}/reconcile`, { method: 'POST', headers: protectedHeaders() })
export const fetchOrders = (signal?: AbortSignal) => requestJson<OrderListResponse>('/api/orders', { signal })

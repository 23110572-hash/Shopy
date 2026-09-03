export type ProductCategory =
  | 'smartphones'
  | 'speakers'
  | 'headphones'
  | 'laptops'
  | 'tablets'

export type AppPage = 'home' | 'cart' | 'profile'

export interface CatalogProduct {
  id: string
  sku: string
  brand: string
  model: string
  category: ProductCategory
  title: string
  description: string
  offer_price_paise: number
  mrp_paise: number | null
  inventory_quantity: number
  in_stock: boolean
  specifications: Record<string, unknown>
  search_tags: string[]
  image_url: string | null
  source_url: string
  specifications_verified_at: string
  version: number
}

export interface CatalogPage {
  items: CatalogProduct[]
  total: number
  limit: number
  offset: number
  category_counts: Record<ProductCategory, number>
}

export interface ProviderHealth {
  provider: string
  status: string
  mode?: string
}

export interface HealthStatus {
  service?: string
  environment?: string
  status: string
  database: string
  openrouter: ProviderHealth
  razorpay: ProviderHealth
}

export interface CartItem {
  product: CatalogProduct
  quantity: number
}

export type AgentIntentSource = 'deterministic' | 'openrouter' | 'deterministic_fallback'

export interface ShoppingIntent {
  query: string
  category: ProductCategory | null
  max_price_paise: number | null
  preferences: string[]
}

export interface AgentRecommendation {
  product: CatalogProduct
  score: number
  reasons: string[]
}

export interface AgentChatRequest {
  message: string
  category?: ProductCategory
  max_price_paise?: number
  limit?: number
}

export type AgentDecisionSource = AgentIntentSource

export type ProposalBlocker = 'AUTH_REQUIRED' | 'PAYMENT_NOT_CONFIGURED' | 'STALE'

export interface ProposalHardLimits {
  requested_or_effective_ceiling_paise: number | null
  recommendation_ceiling_paise: number | null
  per_purchase_limit_paise: number | null
  daily_spend_limit_paise: number | null
  monthly_spend_limit_paise: number | null
}

export interface AgentProductDecision {
  selected_product_id: string
  ranked_product_ids: string[]
  winner_reason: string
  tradeoffs: string[]
  upsell_product_id: string | null
  upsell_reason: string | null
  cross_sell_product_id: string | null
  cross_sell_reason: string | null
  decision_source: AgentDecisionSource
}

export interface PurchaseProposal {
  proposal_id: string
  run_id: string
  product: CatalogProduct
  quantity: 1
  amount_paise: number
  currency: 'INR'
  selection_source: AgentDecisionSource
  selection_reason: string
  product_version: number
  controls_version: number
  expires_at: string
  checkout_available: boolean
  blocker: ProposalBlocker | null
  hard_limits: ProposalHardLimits
}

export type CheckoutAction = 'CREATE_ORDER' | 'OPEN_CHECKOUT' | 'RECONCILE'

export interface CheckoutSession {
  run_id: string
  proposal_id: string
  key_id: string
  order_id: string
  amount_paise: number
  currency: 'INR'
  merchant_name: string
  description: string
  prefill_name: string
  prefill_email: string
  state: 'ORDER_CREATED'
  expires_at: string
  test_mode: true
  allowed_actions: CheckoutAction[]
}

export interface CheckoutCallback {
  razorpay_payment_id: string
  razorpay_order_id: string
  razorpay_signature: string
}

export interface PurchaseRunStatus {
  run_id: string
  proposal_id: string
  state: string
  payment_state: string | null
  order_id: string | null
  payment_id: string | null
  provider_order_status: string | null
  amount_paise: number
  currency: 'INR'
  terminal_reason: string | null
  allowed_actions: CheckoutAction[]
  quote_expires_at: string
  updated_at: string
  retry_after_ms: number | null
  message: string
}

export interface AgentChatResponse {
  agent_name: 'Shopy Agent'
  reply: string
  intent_source: AgentIntentSource
  decision_source?: AgentDecisionSource | null
  parser_notice: string
  intent: ShoppingIntent
  recommendations: AgentRecommendation[]
  winner?: AgentRecommendation | null
  decision?: AgentProductDecision | null
  upsell?: AgentRecommendation | null
  cross_sell?: AgentRecommendation | null
  purchase_proposal?: PurchaseProposal | null
  account_controls_applied: boolean
  catalogue_backed: true
  checkout_available: boolean
  notice: string
}

export type UserRole = 'buyer' | 'merchant_admin'

export interface AccountProfile {
  id: string
  email: string
  display_name: string
  role: UserRole
  email_verified: boolean
  is_active: boolean
  created_at: string
  last_login_at: string | null
}

export interface AuthResponse {
  profile: AccountProfile
  message: string
}

export interface SignupRequest {
  email: string
  display_name: string
  password: string
}

export interface LoginRequest {
  email: string
  password: string
}

export interface AgentControlsUpdate {
  agent_enabled: boolean
  recommendation_price_ceiling_paise: number | null
  per_purchase_limit_paise: number | null
  daily_spend_limit_paise: number | null
  monthly_spend_limit_paise: number | null
  approval_required_above_paise: number | null
  category_allowlist: ProductCategory[]
  max_recommendations: number
  max_replans: number
  allow_substitutions: boolean
}

export interface AgentControls extends AgentControlsUpdate {
  user_id: string
  currency: 'INR'
  version: number
  updated_at: string
  purchase_authority: 'not_active'
  purchase_authority_notice: string
}

export interface OrderHistoryItem {
  order_id: string
  status: string
  amount_paise: number
  currency: 'INR'
  created_at: string
}

export interface TransactionHistoryItem {
  transaction_id: string
  order_id: string
  provider: 'razorpay'
  status: string
  amount_paise: number
  currency: 'INR'
  created_at: string
}

export interface OrderHistoryResponse {
  availability: 'unavailable'
  items: OrderHistoryItem[]
  reason: string
}

export interface TransactionHistoryResponse {
  availability: 'unavailable'
  items: TransactionHistoryItem[]
  reason: string
}

/* ---------------------------------------------------------------- cart orders */

export type PaymentMethod = 'COD' | 'RAZORPAY'
export type OrderStatus = 'PENDING_PAYMENT' | 'CONFIRMED' | 'PAYMENT_FAILED' | 'CANCELLED'
export type OrderPaymentStatus = 'PENDING' | 'PAID' | 'FAILED'

export interface DeliveryAddressInput {
  full_name: string
  phone: string
  line1: string
  line2: string | null
  landmark: string | null
  city: string
  state: string
  postal_code: string
  is_default: boolean
}

export interface DeliveryAddress {
  id: string
  full_name: string
  phone: string
  line1: string
  line2: string | null
  landmark: string | null
  city: string
  state: string
  postal_code: string
  country: 'IN'
  is_default: boolean
  created_at: string
  updated_at: string
}

export interface DeliveryAddressList {
  items: DeliveryAddress[]
}

export interface OrderItem {
  product_id: string
  sku: string
  title: string
  brand: string
  model: string
  category: string
  unit_amount_paise: number
  quantity: number
  line_total_paise: number
}

export interface ShippingAddressSnapshot {
  full_name: string
  phone: string
  line1: string
  line2: string | null
  landmark: string | null
  city: string
  state: string
  postal_code: string
  country: string
}

export interface CustomerOrder {
  id: string
  order_number: string
  status: OrderStatus
  payment_method: PaymentMethod
  payment_status: OrderPaymentStatus
  item_count: number
  subtotal_paise: number
  shipping_paise: number
  total_paise: number
  currency: 'INR'
  shipping_address: ShippingAddressSnapshot
  items: OrderItem[]
  placed_at: string | null
  paid_at: string | null
  failure_reason: string | null
  created_at: string
  message: string
}

export interface RazorpayHandoff {
  key_id: string
  provider_order_id: string
  amount_paise: number
  currency: 'INR'
  merchant_name: string
  description: string
  prefill_name: string
  prefill_email: string
  prefill_contact: string
  test_mode: true
}

export interface PlaceOrderResponse {
  order: CustomerOrder
  razorpay: RazorpayHandoff | null
}

export interface PlaceOrderRequest {
  address_id: string
  payment_method: PaymentMethod
  items: Array<{ product_id: string; quantity: number }>
}

export interface OrderListResponse {
  items: CustomerOrder[]
}

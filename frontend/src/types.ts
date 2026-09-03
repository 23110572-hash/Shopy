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

export interface AgentChatResponse {
  agent_name: 'Shopy Agent'
  reply: string
  intent_source: AgentIntentSource
  parser_notice: string
  intent: ShoppingIntent
  recommendations: AgentRecommendation[]
  account_controls_applied: boolean
  catalogue_backed: true
  checkout_available: false
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

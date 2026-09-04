import { useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import logoPng from './assets/logo.png'
import {
  confirmCheckoutPayment,
  createCheckoutOrder,
  fetchAccountProfile,
  fetchCatalog,
  fetchHealth,
  reconcileCheckoutPayment,
  sendAgentChat,
} from './api'
import {
  CheckoutDismissedError,
  fromCheckoutSession,
  loadRazorpayCheckout,
  openRazorpayCheckout,
} from './razorpay'
import AccountCenter from './AccountCenter'
import Checkout from './Checkout'
import AgentWorkspace from './agent/AgentWorkspace'
import type {
  AccountProfile,
  AgentChatResponse,
  AppPage,
  CartItem,
  CatalogPage,
  CatalogProduct,
  HealthStatus,
  ProductCategory,
  PurchaseProposal,
  PurchaseRunStatus,
} from './types'

const CART_STORAGE_KEY = 'shopy-cart-v1'
const productCategories: readonly ProductCategory[] = [
  'smartphones',
  'speakers',
  'headphones',
  'laptops',
  'tablets',
]
const categories: Array<{ id: ProductCategory | 'all'; label: string; glyph: React.ReactNode }> = [
  { id: 'all', label: 'All products', glyph: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" /></svg> },
  { id: 'smartphones', label: 'Phones', glyph: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><rect x="5" y="2" width="14" height="20" rx="2" ry="2" /><line x1="12" y1="18" x2="12.01" y2="18" /></svg> },
  { id: 'speakers', label: 'Speakers', glyph: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><rect x="4" y="2" width="16" height="20" rx="2" ry="2" /><circle cx="12" cy="14" r="4" /><line x1="12" y1="6" x2="12.01" y2="6" /></svg> },
  { id: 'headphones', label: 'Headphones', glyph: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M3 18v-6a9 9 0 0 1 18 0v6" /><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z" /></svg> },
  { id: 'laptops', label: 'Laptops', glyph: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" ry="2" /><line x1="2" y1="21" x2="22" y2="21" /></svg> },
  { id: 'tablets', label: 'Tablets', glyph: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><rect x="4" y="2" width="16" height="20" rx="2" ry="2" /><line x1="12" y1="18" x2="12.01" y2="18" /></svg> },
]
const categoryGlyphs: Record<ProductCategory, React.ReactNode> = {
  smartphones: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><rect x="5" y="2" width="14" height="20" rx="2" ry="2" /><line x1="12" y1="18" x2="12.01" y2="18" /></svg>,
  speakers: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><rect x="4" y="2" width="16" height="20" rx="2" ry="2" /><circle cx="12" cy="14" r="4" /><line x1="12" y1="6" x2="12.01" y2="6" /></svg>,
  headphones: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M3 18v-6a9 9 0 0 1 18 0v6" /><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z" /></svg>,
  laptops: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" ry="2" /><line x1="2" y1="21" x2="22" y2="21" /></svg>,
  tablets: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><rect x="4" y="2" width="16" height="20" rx="2" ry="2" /><line x1="12" y1="18" x2="12.01" y2="18" /></svg>,
}

type IconName = 'home' | 'cart' | 'profile' | 'sparkles' | 'send' | 'close' | 'check'

function Icon({ name }: { name: IconName }) {
  let content
  if (name === 'home') content = <><path d="m3 10 9-7 9 7" /><path d="M5 9v11h14V9" /><path d="M9 20v-6h6v6" /></>
  else if (name === 'cart') content = <><path d="M3 4h2l2.2 10.2a2 2 0 0 0 2 1.6h7.9a2 2 0 0 0 2-1.6L20.5 8H6" /><circle cx="10" cy="20" r="1" /><circle cx="18" cy="20" r="1" /></>
  else if (name === 'profile') content = <><circle cx="12" cy="8" r="4" /><path d="M4.5 21a7.5 7.5 0 0 1 15 0" /></>
  else if (name === 'sparkles') content = <><path d="m12 3 1.2 3.8L17 8l-3.8 1.2L12 13l-1.2-3.8L7 8l3.8-1.2L12 3Z" /><path d="m19 14 .7 2.3L22 17l-2.3.7L19 20l-.7-2.3L16 17l2.3-.7L19 14Z" /></>
  else if (name === 'send') content = <><path d="m22 2-7 20-4-9-9-4 20-7Z" /><path d="M22 2 11 13" /></>
  else if (name === 'close') content = <><path d="m6 6 12 12" /><path d="M18 6 6 18" /></>
  else content = <path d="m5 12 4 4L19 6" />
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{content}</svg>
}

function formatPrice(paise: number): string {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(paise / 100)
}

function isCatalogProduct(value: unknown): value is CatalogProduct {
  if (typeof value !== 'object' || value === null) return false
  const product = value as Partial<CatalogProduct>
  return (
    typeof product.id === 'string' &&
    typeof product.sku === 'string' &&
    typeof product.brand === 'string' &&
    typeof product.model === 'string' &&
    typeof product.title === 'string' &&
    typeof product.description === 'string' &&
    productCategories.includes(product.category as ProductCategory) &&
    typeof product.offer_price_paise === 'number' &&
    (product.mrp_paise === null || typeof product.mrp_paise === 'number') &&
    typeof product.inventory_quantity === 'number' &&
    Number.isInteger(product.inventory_quantity) &&
    typeof product.in_stock === 'boolean' &&
    typeof product.specifications === 'object' &&
    product.specifications !== null &&
    Array.isArray(product.search_tags) &&
    product.search_tags.every((tag) => typeof tag === 'string') &&
    (product.image_url === null || typeof product.image_url === 'string') &&
    typeof product.source_url === 'string' &&
    typeof product.specifications_verified_at === 'string' &&
    typeof product.version === 'number' &&
    Number.isInteger(product.version)
  )
}

function loadCart(): CartItem[] {
  try {
    const stored = window.localStorage.getItem(CART_STORAGE_KEY)
    if (!stored) return []
    const parsed: unknown = JSON.parse(stored)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((value): value is CartItem => {
      if (typeof value !== 'object' || value === null) return false
      const item = value as { product?: unknown; quantity?: unknown }
      return isCatalogProduct(item.product) && typeof item.quantity === 'number' && Number.isInteger(item.quantity) && item.quantity > 0
    })
  } catch {
    return []
  }
}

function messageId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

interface NavigationProps {
  page: AppPage
  cartCount: number
  online: boolean
  onNavigate: (page: AppPage) => void
}

function Navigation({ page, cartCount, online, onNavigate }: NavigationProps) {
  const links: Array<{ page: AppPage; label: string; icon: IconName }> = [
    { page: 'home', label: 'Home', icon: 'home' },
    { page: 'agent', label: 'Agent', icon: 'sparkles' },
    { page: 'cart', label: 'Cart', icon: 'cart' },
    { page: 'profile', label: 'Profile', icon: 'profile' },
  ]
  return (
    <header className="nav-shell">
      <button className="wordmark" type="button" onClick={() => onNavigate('home')} aria-label="Shopy home">
        <img src={logoPng} alt="Shopy" className="wordmark-logo" />
      </button>
      <nav className="glass-nav" aria-label="Main navigation">
        {links.map((link) => (
          <button
            key={link.page}
            type="button"
            className={page === link.page ? 'nav-item active' : 'nav-item'}
            onClick={() => onNavigate(link.page)}
            aria-current={page === link.page ? 'page' : undefined}
          >
            <Icon name={link.icon} /><span>{link.label}</span>
            {link.page === 'cart' && cartCount > 0 ? <b>{cartCount}</b> : null}
          </button>
        ))}
      </nav>
    </header>
  )
}

function catalogImageSrc(sku: string): string {
  return `/catalog/${sku}.jpg`
}

function CatalogImage({ product, imgClassName, fallback }: { product: CatalogProduct; imgClassName: string; fallback: React.ReactNode }) {
  const [error, setError] = useState(false)
  if (error) return <>{fallback}</>
  return (
    <img
      src={catalogImageSrc(product.sku)}
      alt={product.title}
      className={imgClassName}
      loading="lazy"
      onError={() => setError(true)}
    />
  )
}

function ProductCard({ product, onAdd }: { product: CatalogProduct; onAdd: (product: CatalogProduct) => void }) {
  const discount = product.mrp_paise
    ? Math.max(0, Math.round((1 - product.offer_price_paise / product.mrp_paise) * 100))
    : 0
  return (
    <article className="product-card">
      <div className={`product-visual visual-${product.category}`}>
        <CatalogImage
          product={product}
          imgClassName="product-image"
          fallback={<span className="visual-glyph" aria-hidden="true">{categoryGlyphs[product.category]}</span>}
        />
        <span className="brand-chip">{product.brand}</span>
        {discount > 0 ? <span className="discount-chip">−{discount}%</span> : null}
        <i className="orbit orbit-one" /><i className="orbit orbit-two" />
      </div>
      <div className="product-content">
        <div className="product-kicker">
          <span>{product.category}</span>
          <span className={product.in_stock ? 'inventory available' : 'inventory'}>
            {product.in_stock ? `${product.inventory_quantity} in stock` : 'Out of stock'}
          </span>
        </div>
        <h3>{product.title}</h3>
        <p>{product.description}</p>
        <div className="product-price">
          <strong>{formatPrice(product.offer_price_paise)}</strong>
          {product.mrp_paise && product.mrp_paise > product.offer_price_paise ? <s>{formatPrice(product.mrp_paise)}</s> : null}
        </div>
        <div className="product-actions">
          <a href={product.source_url} target="_blank" rel="noreferrer">Official details ↗</a>
          <button type="button" onClick={() => onAdd(product)} disabled={!product.in_stock}>
            {product.in_stock ? 'Add to cart' : 'Unavailable'}
          </button>
        </div>
      </div>
    </article>
  )
}

interface HomeProps {
  catalog: CatalogPage | null
  query: string
  category: ProductCategory | 'all'
  loading: boolean
  error: string | null
  onQuery: (value: string) => void
  onCategory: (value: ProductCategory | 'all') => void
  onAdd: (product: CatalogProduct) => void
  onAgent: () => void
}

function HomeView({ catalog, query, category, loading, error, onQuery, onCategory, onAdd, onAgent }: HomeProps) {
  const productTotal = catalog?.category_counts
    ? Object.values(catalog.category_counts).reduce((sum, count) => sum + count, 0)
    : 0
  return (
    <main className="page home-page">
      <section className="catalog-section" id="catalog">
        <div className="section-intro" style={{ marginTop: '40px' }}>
          <label className="catalog-search">
            <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></svg>
            <input value={query} onChange={(event) => onQuery(event.target.value)} placeholder="Search brand, model or feature" aria-label="Search products" />
            {query ? <button type="button" onClick={() => onQuery('')} aria-label="Clear search">×</button> : null}
          </label>
        </div>
        <div className="category-list" role="list" aria-label="Product categories">
          {categories.map((item) => {
            const count = item.id === 'all' ? productTotal : (catalog?.category_counts[item.id] ?? 0)
            return <button type="button" key={item.id} className={category === item.id ? 'category-button selected' : 'category-button'} onClick={() => onCategory(item.id)}><span>{item.glyph}</span><strong>{item.label}</strong><small>{count}</small></button>
          })}
        </div>
        <div className="results-bar"><span>{loading ? 'Refreshing live products…' : `${catalog?.total ?? 0} products`}</span><span>Merchant inventory · INR pricing</span></div>
        {error ? <div className="state-card error"><strong>Catalogue unavailable</strong><p>{error}</p></div> : null}
        {!error && !loading && catalog?.items.length === 0 ? <div className="state-card"><strong>No matching products</strong><p>Try a broader search or another category.</p></div> : null}
        <div className={loading ? 'product-grid loading' : 'product-grid'}>
          {catalog?.items.map((product) => <ProductCard key={product.id} product={product} onAdd={onAdd} />)}
        </div>
      </section>
    </main>
  )
}

interface CartProps {
  cart: CartItem[]
  onQuantity: (id: string, quantity: number) => void
  onRemove: (id: string) => void
  onBrowse: () => void
  onClear: () => void
  onSignIn: () => void
  signedIn: boolean
  sessionChecked: boolean
}

function CartView({
  cart,
  onQuantity,
  onRemove,
  onBrowse,
  onClear,
  onSignIn,
  signedIn,
  sessionChecked,
}: CartProps) {
  const total = cart.reduce((sum, item) => sum + item.product.offer_price_paise * item.quantity, 0)
  const count = cart.reduce((sum, item) => sum + item.quantity, 0)
  const [checkingOut, setCheckingOut] = useState(false)

  if (checkingOut && cart.length > 0) {
    return (
      <main className="page interior-page">
        <section className="page-heading"><span className="section-label">SECURE CHECKOUT</span><h1>Checkout</h1><p>Confirm your delivery address and choose how you want to pay.</p></section>
        <Checkout
          cart={cart}
          onOrderConfirmed={onClear}
          onBack={() => setCheckingOut(false)}
          onSignIn={onSignIn}
        />
      </main>
    )
  }

  return (
    <main className="page interior-page">
      <section className="page-heading"><span className="section-label">YOUR SAVED PICKS</span><h1>Your cart</h1><p>Saved all your favourite items.</p></section>
      {cart.length === 0 ? (
        <section className="empty-cart"><div className="empty-icon"><Icon name="cart" /></div><h2>Your cart is ready for something good.</h2><p>Browse the catalogue or ask Shopy Agent to buy for you.</p><button className="primary-action" type="button" onClick={onBrowse}>Start shopping →</button></section>
      ) : (
        <section className="cart-layout">
          <div className="cart-list">
            {cart.map(({ product, quantity }) => (
              <article className="cart-row" key={product.id}>
                <div className={`cart-art visual-${product.category}`}><CatalogImage product={product} imgClassName="cart-image" fallback={categoryGlyphs[product.category]} /></div>
                <div className="cart-info"><span>{product.brand} · {product.category}</span><h2>{product.title}</h2><p>{formatPrice(product.offer_price_paise)} each · {product.inventory_quantity} currently in stock</p><button type="button" onClick={() => onRemove(product.id)}>Remove</button></div>
                <div className="quantity-control"><button type="button" onClick={() => onQuantity(product.id, quantity - 1)} aria-label="Decrease quantity">−</button><span>{quantity}</span><button type="button" onClick={() => onQuantity(product.id, quantity + 1)} disabled={quantity >= product.inventory_quantity} aria-label="Increase quantity">+</button></div>
                <strong className="line-total">{formatPrice(product.offer_price_paise * quantity)}</strong>
              </article>
            ))}
          </div>
          <aside className="order-card"><span className="section-label">CART SUMMARY</span><div><span>Items</span><strong>{count}</strong></div><div><span>Delivery</span><strong>Free</strong></div><div className="order-total"><span>Order total</span><strong>{formatPrice(total)}</strong></div>
            {signedIn ? (
              <>
                <button type="button" onClick={() => setCheckingOut(true)}>Proceed to checkout →</button>
              </>
            ) : (
              <>
                <button type="button" disabled title="Sign in to place an order">Sign in to checkout</button>
                <p>{sessionChecked ? 'Orders are saved to your Shopy account, so checkout needs you signed in.' : 'Checking your session…'}</p>
                <button className="signin-cta" type="button" onClick={onSignIn}>Sign in or create account →</button>
              </>
            )}
            <button className="continue-button" type="button" onClick={onBrowse}>← Continue shopping</button></aside>
        </section>
      )}
    </main>
  )
}

interface ChatMessage {
  id: string
  role: 'user' | 'agent'
  text: string
  response?: AgentChatResponse
}

type CheckoutPhase = 'idle' | 'creating' | 'opening' | 'confirming' | 'settled'

const blockerCopy: Record<string, string> = {
  AUTH_REQUIRED: 'Sign in from the Profile tab to turn this quote into a Razorpay Test order.',
  PAYMENT_NOT_CONFIGURED: 'Razorpay Test Mode is not configured on the server yet.',
  STALE: 'This product changed after the quote was saved. Ask Shopy to compare again.',
}

function remainingLabel(expiresAt: string, now: number): string | null {
  const remaining = new Date(expiresAt).getTime() - now
  if (!Number.isFinite(remaining) || remaining <= 0) return null
  const totalSeconds = Math.floor(remaining / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

function ProposalCheckout({ proposal }: { proposal: PurchaseProposal }) {
  const [phase, setPhase] = useState<CheckoutPhase>('idle')
  const [status, setStatus] = useState<PurchaseRunStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())

  const settled = status?.state === 'CAPTURED' || status?.state === 'PAYMENT_FAILED'

  useEffect(() => {
    if (settled) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [settled])

  const countdown = remainingLabel(proposal.expires_at, now)
  const expired = countdown === null
  const busy = phase === 'creating' || phase === 'opening' || phase === 'confirming'
  const captured = status?.state === 'CAPTURED'
  const failed = status?.state === 'PAYMENT_FAILED'
  const pending = status !== null && !captured && !failed

  async function pay() {
    setError(null)
    setPhase('creating')
    let runId: string | null = null
    try {
      const [session] = await Promise.all([
        createCheckoutOrder(proposal.proposal_id),
        loadRazorpayCheckout(),
      ])
      runId = session.run_id
      setPhase('opening')
      const callback = await openRazorpayCheckout(fromCheckoutSession(session))
      setPhase('confirming')
      setStatus(await confirmCheckoutPayment(session.run_id, callback))
      setPhase('settled')
    } catch (flowError) {
      // A dismissed modal is not a failure. Ask the server for the real state.
      if (flowError instanceof CheckoutDismissedError && runId !== null) {
        try {
          setPhase('confirming')
          setStatus(await reconcileCheckoutPayment(runId))
          setPhase('settled')
          return
        } catch {
          setError('Checkout was closed. Use "Check payment status" to confirm.')
          setPhase('idle')
          return
        }
      }
      setError(
        flowError instanceof Error ? flowError.message : 'Checkout could not be completed.',
      )
      setPhase('idle')
    }
  }

  async function recheck() {
    const runId = status?.run_id
    if (!runId) return
    setError(null)
    setPhase('confirming')
    try {
      setStatus(await reconcileCheckoutPayment(runId))
    } catch (statusError) {
      setError(
        statusError instanceof Error ? statusError.message : 'Payment status is unavailable.',
      )
    } finally {
      setPhase('settled')
    }
  }

  if (captured) {
    return (
      <div className="proposal-checkout captured">
        <strong>Payment captured · Test Mode</strong>
        <p>{status?.message ?? 'Razorpay confirmed this test payment.'}</p>
        <small>Order {status?.order_id ?? '—'} · Payment {status?.payment_id ?? '—'}</small>
      </div>
    )
  }

  const blocked = !proposal.checkout_available
  const blockedReason = proposal.blocker ? blockerCopy[proposal.blocker] : null

  return (
    <div className="proposal-checkout">
      <div className="proposal-head">
        <span>BEST MATCH · BOUNDED QUOTE</span>
        <strong>{proposal.product.title}</strong>
        <b>{formatPrice(proposal.amount_paise)}</b>
      </div>
      {blocked ? (
        <p className="proposal-note">
          {blockedReason ?? 'Checkout is not available for this quote yet.'}
        </p>
      ) : expired ? (
        <p className="proposal-note">This quote expired. Ask Shopy to compare again.</p>
      ) : (
        <>
          <button className="proposal-pay" type="button" onClick={pay} disabled={busy}>
            {phase === 'creating'
              ? 'Creating Razorpay order…'
              : phase === 'opening'
                ? 'Waiting for Razorpay…'
                : phase === 'confirming'
                  ? 'Verifying payment…'
                  : `Pay ${formatPrice(proposal.amount_paise)} in Test Mode`}
          </button>
          <small className="proposal-note">
            Razorpay Test Mode · no real money moves · quote expires in {countdown}
          </small>
        </>
      )}
      {failed ? (
        <p className="proposal-error">
          {status?.terminal_reason ?? 'Razorpay reported that the payment failed.'}
        </p>
      ) : null}
      {pending ? (
        <>
          <p className="proposal-note">{status?.message}</p>
          <button className="proposal-recheck" type="button" onClick={recheck} disabled={busy}>
            Check payment status
          </button>
        </>
      ) : null}
      {error ? <p className="proposal-error">{error}</p> : null}
    </div>
  )
}

function FloatingAgent({ open, onOpen, onAdd }: { open: boolean; onOpen: (value: boolean) => void; onAdd: (product: CatalogProduct) => void }) {
  const [messages, setMessages] = useState<ChatMessage[]>([{ id: 'welcome', role: 'agent', text: 'Hi — I search the live Shopy catalogue. Tell me a category, feature, or budget.' }])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (open) endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, open])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const text = draft.trim()
    if (!text || busy) return
    setMessages((current) => [...current, { id: messageId(), role: 'user', text }])
    setDraft('')
    setBusy(true)
    try {
      const response = await sendAgentChat({ message: text, limit: 4 })
      setMessages((current) => [...current, { id: messageId(), role: 'agent', text: response.reply, response }])
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'The agent endpoint is unavailable.'
      setMessages((current) => [...current, { id: messageId(), role: 'agent', text: `I could not search the catalogue: ${detail}` }])
    } finally {
      setBusy(false)
    }
  }

  const untouched = !messages.some((message) => message.role === 'user')
  return (
    <aside className="agent-widget" aria-label="Shopy Agent">
      {open ? (
        <section className="agent-panel">
          <header><div className="agent-identity"><span><Icon name="sparkles" /></span><div><strong>Shopy Agent</strong><small><i /> Live catalogue search</small></div></div><button type="button" onClick={() => onOpen(false)} aria-label="Close Shopy Agent"><Icon name="close" /></button></header>
          <div className="agent-messages" aria-live="polite">
            {messages.map((message) => (
              <div className={`chat-message ${message.role}`} key={message.id}>
                <div className="chat-bubble">{message.text}</div>
                {message.response ? <><div className="agent-results">{message.response.recommendations.map((recommendation) => <article key={recommendation.product.id}><div className={`result-art visual-${recommendation.product.category}`}><CatalogImage product={recommendation.product} imgClassName="result-image" fallback={categoryGlyphs[recommendation.product.category]} /></div><div><span>{recommendation.product.brand} · {recommendation.score}/100</span><strong>{recommendation.product.title}</strong><small>{recommendation.reasons.slice(0, 2).join(' · ')}</small><b>{formatPrice(recommendation.product.offer_price_paise)}</b></div><button type="button" onClick={() => onAdd(recommendation.product)} aria-label={`Add ${recommendation.product.title} to cart`}>+</button></article>)}</div>{message.response.purchase_proposal ? <ProposalCheckout proposal={message.response.purchase_proposal} /> : null}{message.response.notice ? <small className="agent-notice">{message.response.notice}</small> : null}</> : null}
              </div>
            ))}
            {busy ? <div className="agent-typing" aria-label="Shopy Agent is searching"><span /><span /><span /></div> : null}
            {untouched ? <div className="quick-prompts"><button type="button" onClick={() => setDraft('Find wireless headphones under ₹20,000')}>Headphones under ₹20k</button><button type="button" onClick={() => setDraft('Show me laptops under ₹80,000')}>Laptops under ₹80k</button></div> : null}
            <div ref={endRef} />
          </div>
          <form className="agent-input" onSubmit={submit}><input value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Ask for a product…" aria-label="Message Shopy Agent" maxLength={1000} /><button type="submit" disabled={!draft.trim() || busy} aria-label="Send message"><Icon name="send" /></button></form>
          <footer>Live catalogue search <span>•</span> Razorpay Test Mode only</footer>
        </section>
      ) : null}
      <button className="agent-launcher" type="button" onClick={() => onOpen(!open)} aria-label={open ? 'Close Shopy Agent' : 'Open Shopy Agent'} aria-expanded={open}>{open ? <Icon name="close" /> : <Icon name="sparkles" />}{!open ? <span className="launcher-pulse" /> : null}</button>
    </aside>
  )
}

function App() {
  const [page, setPage] = useState<AppPage>('home')
  const [catalog, setCatalog] = useState<CatalogPage | null>(null)
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState<ProductCategory | 'all'>('all')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [cart, setCart] = useState<CartItem[]>(loadCart)
  const [agentOpen, setAgentOpen] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const [profile, setProfile] = useState<AccountProfile | null>(null)
  const [sessionChecked, setSessionChecked] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    fetchHealth(controller.signal).then(setHealth).catch(() => setHealth(null))
    return () => controller.abort()
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    fetchAccountProfile(controller.signal)
      .then(setProfile)
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        setProfile(null)
      })
      .finally(() => setSessionChecked(true))
    return () => controller.abort()
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      setLoading(true)
      setError(null)
      fetchCatalog(query, category, controller.signal)
        .then(setCatalog)
        .catch((requestError: unknown) => {
          if (requestError instanceof DOMException && requestError.name === 'AbortError') return
          setError('Start the FastAPI backend, then refresh this page.')
        })
        .finally(() => setLoading(false))
    }, 180)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [query, category])

  useEffect(() => {
    try { window.localStorage.setItem(CART_STORAGE_KEY, JSON.stringify(cart)) } catch { /* Keep the cart in memory if browser storage is unavailable. */ }
  }, [cart])

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(null), 2200)
    return () => window.clearTimeout(timer)
  }, [toast])

  const cartCount = useMemo(() => cart.reduce((sum, item) => sum + item.quantity, 0), [cart])

  function navigate(next: AppPage) {
    setPage(next)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  function addToCart(product: CatalogProduct) {
    if (!product.in_stock || product.inventory_quantity < 1) return
    setCart((current) => {
      const existing = current.find((item) => item.product.id === product.id)
      if (!existing) return [...current, { product, quantity: 1 }]
      return current.map((item) => item.product.id === product.id ? { ...item, product, quantity: Math.min(item.quantity + 1, product.inventory_quantity) } : item)
    })
    setToast(`${product.title} added to cart`)
  }

  function changeQuantity(id: string, quantity: number) {
    if (quantity <= 0) { setCart((current) => current.filter((item) => item.product.id !== id)); return }
    setCart((current) => current.map((item) => item.product.id === id ? { ...item, quantity: Math.min(quantity, item.product.inventory_quantity) } : item))
  }

  return (
    <div className="app-shell">
      <Navigation page={page} cartCount={cartCount} online={health?.database === 'ready'} onNavigate={navigate} />
      {page === 'home' ? <HomeView catalog={catalog} query={query} category={category} loading={loading} error={error} onQuery={setQuery} onCategory={setCategory} onAdd={addToCart} onAgent={() => navigate('agent')} /> : null}
      {page === 'agent' ? <AgentWorkspace profile={profile} sessionChecked={sessionChecked} onSignIn={() => navigate('profile')} onAddToCart={addToCart} /> : null}
      {page === 'cart' ? <CartView cart={cart} onQuantity={changeQuantity} onRemove={(id) => setCart((current) => current.filter((item) => item.product.id !== id))} onBrowse={() => navigate('home')} onClear={() => setCart([])} onSignIn={() => navigate('profile')} signedIn={profile !== null} sessionChecked={sessionChecked} /> : null}
      {page === 'profile' ? <AccountCenter health={health} onSession={setProfile} /> : null}
      <footer className="site-footer">
        <button type="button" onClick={() => navigate('home')} aria-label="Shopy home">
          <img src={logoPng} alt="Shopy" className="footer-logo" />
        </button>
        <a href="https://shopy-zewo.onrender.com/docs" target="_blank" rel="noreferrer">API docs ↗</a>
      </footer>
      {toast ? <div className="cart-toast"><Icon name="check" />{toast}</div> : null}
      {page !== 'agent' ? <FloatingAgent open={agentOpen} onOpen={(value) => { setAgentOpen(false); if (value) navigate('agent') }} onAdd={addToCart} /> : null}
    </div>
  )
}

export default App

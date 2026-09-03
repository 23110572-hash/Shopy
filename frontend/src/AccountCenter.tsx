import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import logoPng from './assets/logo.png'
import {
  ApiError,
  fetchAccountProfile,
  fetchAgentControls,
  fetchOrderHistory,
  fetchTransactionHistory,
  loginAccount,
  logoutAccount,
  signupAccount,
  updateAccountProfile,
  updateAgentControls,
} from './api'
import type {
  AccountProfile,
  AgentControls,
  AgentControlsUpdate,
  HealthStatus,
  OrderHistoryResponse,
  ProductCategory,
  TransactionHistoryResponse,
} from './types'

const categoryChoices: Array<{ id: ProductCategory; label: string; glyph: React.ReactNode }> = [
  { id: 'smartphones', label: 'Phones', glyph: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><rect x="5" y="2" width="14" height="20" rx="2" ry="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg> },
  { id: 'speakers', label: 'Speakers', glyph: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><rect x="4" y="2" width="16" height="20" rx="2" ry="2"/><circle cx="12" cy="14" r="4"/><line x1="12" y1="6" x2="12.01" y2="6"/></svg> },
  { id: 'headphones', label: 'Headphones', glyph: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/></svg> },
  { id: 'laptops', label: 'Laptops', glyph: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="2" y1="21" x2="22" y2="21"/></svg> },
  { id: 'tablets', label: 'Tablets', glyph: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><rect x="4" y="2" width="16" height="20" rx="2" ry="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg> },
]

type AccountTab = 'auth' | 'overview' | 'orders' | 'transactions' | 'agent' | 'security'
type AuthMode = 'login' | 'signup'

type SessionState = 'loading' | 'guest' | 'authenticated'

interface AccountCenterProps {
  health: HealthStatus | null
}

function formatDate(value: string | null): string {
  if (!value) return 'Not yet'
  return new Intl.DateTimeFormat('en-IN', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  }).format(new Date(value))
}

function formatPrice(paise: number): string {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(paise / 100)
}

function controlsPayload(controls: AgentControls): AgentControlsUpdate {
  return {
    agent_enabled: controls.agent_enabled,
    recommendation_price_ceiling_paise: controls.recommendation_price_ceiling_paise,
    per_purchase_limit_paise: controls.per_purchase_limit_paise,
    daily_spend_limit_paise: controls.daily_spend_limit_paise,
    monthly_spend_limit_paise: controls.monthly_spend_limit_paise,
    approval_required_above_paise: controls.approval_required_above_paise,
    category_allowlist: controls.category_allowlist,
    max_recommendations: controls.max_recommendations,
    max_replans: controls.max_replans,
    allow_substitutions: controls.allow_substitutions,
  }
}

function MoneyInput({
  label,
  hint,
  value,
  onChange,
}: {
  label: string
  hint: string
  value: number | null
  onChange: (value: number | null) => void
}) {
  return (
    <label className="control-field money-control">
      <span>{label}</span>
      <div><b>₹</b><input type="number" min="1" step="1" value={value === null ? '' : value / 100} onChange={(event) => {
        const rupees = event.target.value === '' ? null : Number(event.target.value)
        onChange(rupees === null || !Number.isFinite(rupees) || rupees <= 0 ? null : Math.round(rupees * 100))
      }} placeholder="Not set" /></div>
      <small>{hint}</small>
    </label>
  )
}

function AuthPortal({ onAuthenticated }: { onAuthenticated: (profile: AccountProfile) => void }) {
  const [mode, setMode] = useState<AuthMode>('login')
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    if (mode === 'signup' && password !== confirmation) {
      setError('Passwords do not match.')
      return
    }
    setBusy(true)
    try {
      const result = mode === 'signup'
        ? await signupAccount({ email, display_name: displayName, password })
        : await loginAccount({ email, password })
      onAuthenticated(result.profile)
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Account request failed.')
    } finally {
      setBusy(false)
    }
  }

  function switchMode(nextMode: AuthMode) {
    setMode(nextMode)
    setError(null)
    setPassword('')
    setConfirmation('')
  }

  return (
    <section className="account-panel auth-panel">
      <div className="auth-logo">
        <img src={logoPng} alt="Shopy" className="auth-brand-logo" />
        <span>Account</span>
      </div>
      <div className="auth-switch" role="tablist" aria-label="Account access">
        <button type="button" role="tab" aria-selected={mode === 'login'} className={mode === 'login' ? 'active' : ''} onClick={() => switchMode('login')}>Log in</button>
        <button type="button" role="tab" aria-selected={mode === 'signup'} className={mode === 'signup' ? 'active' : ''} onClick={() => switchMode('signup')}>Create account</button>
      </div>
      <div className="auth-title">
        <span>{mode === 'login' ? 'WELCOME BACK' : 'JOIN SHOPY'}</span>
        <h2>{mode === 'login' ? 'Sign in to your account' : 'Create your shopping profile'}</h2>
        <p>{mode === 'login' ? 'Access saved limits and account activity.' : 'Your account is saved in the real Shopy database.'}</p>
      </div>
      <form className="auth-form" onSubmit={submit}>
        {mode === 'signup' ? <label><span>Full name</span><input required minLength={2} maxLength={120} autoComplete="name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="Your name" /></label> : null}
        <label><span>Email address</span><input required type="email" maxLength={320} autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@example.com" /></label>
        <label><span>Password</span><div className="password-input"><input required minLength={mode === 'signup' ? 10 : 1} maxLength={128} type={showPassword ? 'text' : 'password'} autoComplete={mode === 'signup' ? 'new-password' : 'current-password'} value={password} onChange={(event) => setPassword(event.target.value)} placeholder={mode === 'signup' ? '10+ characters, letter and number' : 'Your password'} /><button type="button" onClick={() => setShowPassword((current) => !current)}>{showPassword ? 'Hide' : 'Show'}</button></div></label>
        {mode === 'signup' ? <label><span>Confirm password</span><input required minLength={10} maxLength={128} type={showPassword ? 'text' : 'password'} autoComplete="new-password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} placeholder="Repeat your password" /></label> : null}
        {error ? <div className="auth-error">{error}</div> : null}
        <button className="auth-submit" type="submit" disabled={busy}>{busy ? 'Please wait…' : mode === 'login' ? 'Log in securely →' : 'Create account →'}</button>
      </form>
      <p className="auth-legal">Shopy stores a password hash—not your password. Payment credentials are never collected here.</p>
    </section>
  )
}

function HistoryEmpty({ title, reason, kind }: { title: string; reason: string; kind: 'order' | 'transaction' }) {
  const orderSvg = <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M6 2L3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/><line x1="3" y1="6" x2="21" y2="6"/><path d="M16 10a4 4 0 0 1-8 0"/></svg>
  const txSvg = <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><rect x="2" y="5" width="20" height="14" rx="2"/><line x1="2" y1="10" x2="22" y2="10"/></svg>

  return (
    <div className="history-empty">
      <div className="history-empty-icon">{kind === 'order' ? orderSvg : txSvg}</div>
      <span>PROVIDER-BACKED HISTORY</span>
      <h3>{title}</h3>
      <p>{reason}</p>
      <div className="history-rule"><b>Why is this empty?</b><small>Cart items and agent recommendations are not purchases. Shopy records history only after an authenticated provider workflow confirms it.</small></div>
    </div>
  )
}

function AccountCenter({ health }: AccountCenterProps) {
  const [sessionState, setSessionState] = useState<SessionState>('loading')
  const [profile, setProfile] = useState<AccountProfile | null>(null)
  const [activeTab, setActiveTab] = useState<AccountTab>('auth')
  const [controls, setControls] = useState<AgentControls | null>(null)
  const [draftControls, setDraftControls] = useState<AgentControlsUpdate | null>(null)
  const [orders, setOrders] = useState<OrderHistoryResponse | null>(null)
  const [transactions, setTransactions] = useState<TransactionHistoryResponse | null>(null)
  const [dataError, setDataError] = useState<string | null>(null)
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved'>('idle')
  const [displayName, setDisplayName] = useState('')
  const [profileSaving, setProfileSaving] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    fetchAccountProfile(controller.signal)
      .then((account) => {
        setProfile(account)
        setDisplayName(account.display_name)
        setSessionState('authenticated')
        setActiveTab((current) => current === 'auth' ? 'overview' : current)
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        // Do not show 'Failed to fetch' error banner to the user just because they are not logged in or API is offline
        setSessionState('guest')
        setActiveTab('auth')
      })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (sessionState !== 'authenticated' || profile === null) return
    const controller = new AbortController()
    setDataError(null)
    Promise.all([
      fetchAgentControls(controller.signal),
      fetchOrderHistory(controller.signal),
      fetchTransactionHistory(controller.signal),
    ])
      .then(([savedControls, orderHistory, transactionHistory]) => {
        setControls(savedControls)
        setDraftControls(controlsPayload(savedControls))
        setOrders(orderHistory)
        setTransactions(transactionHistory)
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        setDataError(error instanceof Error ? error.message : 'Could not load account data.')
      })
    return () => controller.abort()
  }, [profile?.id, sessionState])

  const initials = useMemo(() => profile?.display_name.split(' ').map((part) => part[0]).slice(0, 2).join('').toUpperCase() ?? 'S', [profile])

  async function saveControls(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!draftControls) return
    setSaveState('saving')
    setDataError(null)
    try {
      const saved = await updateAgentControls(draftControls)
      setControls(saved)
      setDraftControls(controlsPayload(saved))
      setSaveState('saved')
      window.setTimeout(() => setSaveState('idle'), 1800)
    } catch (error) {
      setDataError(error instanceof Error ? error.message : 'Could not save agent controls.')
      setSaveState('idle')
    }
  }

  async function saveProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setProfileSaving(true)
    setDataError(null)
    try {
      const updated = await updateAccountProfile(displayName)
      setProfile(updated)
      setDisplayName(updated.display_name)
    } catch (error) {
      setDataError(error instanceof Error ? error.message : 'Could not update profile.')
    } finally {
      setProfileSaving(false)
    }
  }

  async function signOut() {
    setDataError(null)
    try {
      await logoutAccount()
      setProfile(null)
      setControls(null)
      setDraftControls(null)
      setOrders(null)
      setTransactions(null)
      setSessionState('guest')
      setActiveTab('auth')
    } catch (error) {
      setDataError(error instanceof Error ? error.message : 'Could not sign out.')
    }
  }

  if (sessionState === 'loading') {
    return <main className="account-loading"><div className="account-spinner" /><strong>Opening your Shopy account…</strong></main>
  }

  const tabs: Array<{ id: AccountTab; label: string; glyph: string }> = [
    { id: sessionState === 'guest' ? 'auth' : 'overview', label: sessionState === 'guest' ? 'Log in / Sign up' : 'Account overview', glyph: sessionState === 'guest' ? '→' : '○' },
    { id: 'orders', label: 'Your orders', glyph: '▤' },
    { id: 'transactions', label: 'Transactions', glyph: '↔' },
    { id: 'agent', label: 'Agent controls', glyph: '✦' },
    { id: 'security', label: 'Login & security', glyph: '◇' },
  ]

  return (
    <main className="account-page">
      <section className="account-hero">
        <div>
          <span className="section-label">{sessionState === 'guest' ? 'YOUR SHOPPING SPACE' : 'YOUR SHOPY ACCOUNT'}</span>
          <h1>{sessionState === 'authenticated' && profile ? <>Hello, {profile.display_name.split(' ')[0]}.</> : <>One account.<br /><em>Your rules.</em></>}</h1>
          <p>{sessionState === 'authenticated' ? 'Manage account details, real purchase history, and exactly how much authority your shopping agent can use.' : 'Save agent limits, protect purchase authority, and keep real order history together—without invented transactions.'}</p>
        </div>
        {sessionState === 'authenticated' && profile ? (
          <div className="account-hero-badge"><span>{initials}</span><div><small>SIGNED IN AS</small><strong>{profile.email}</strong></div></div>
        ) : null}
      </section>

      <section className="account-workspace">
        <nav className="account-horizontal-nav" aria-label="Account sections">
          {tabs.map((tab) => {
            const isLocked = sessionState === 'guest' && tab.id !== 'auth'
            return (
              <button key={tab.id} type="button" className={activeTab === tab.id ? 'active' : ''} disabled={isLocked} onClick={() => setActiveTab(tab.id)}>
                <span className={isLocked ? 'locked-badge' : ''}>{isLocked ? <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="11" width="18" height="11" rx="2.5" ry="2.5" /><path d="M7 11V7a5 5 0 0 1 10 0v4" /></svg> : tab.glyph}</span>{tab.label}
                {tab.id === 'agent' && controls?.agent_enabled ? <i /> : null}
              </button>
            )
          })}
          {sessionState === 'authenticated' ? <button className="signout-button" type="button" onClick={signOut}>Sign out <span>→</span></button> : null}
        </nav>

        <div className="account-content">
          {dataError ? <div className="account-error">{dataError}</div> : null}

          {activeTab === 'auth' && sessionState === 'guest' ? (
            <AuthPortal onAuthenticated={(account) => {
              setProfile(account)
              setDisplayName(account.display_name)
              setSessionState('authenticated')
              setActiveTab('overview')
            }} />
          ) : null}

          {activeTab === 'overview' && profile ? <section className="account-panel overview-panel">
            <header><div><span>ACCOUNT OVERVIEW</span><h2>Your details</h2><p>Identity data stored in Shopy's account database.</p></div><b className={profile.is_active ? 'account-state active' : 'account-state'}>{profile.is_active ? 'Active' : 'Disabled'}</b></header>
            <div className="overview-metrics"><article><span>▤</span><div><small>Orders</small><strong>{orders?.items.length ?? '—'}</strong><p>Provider-confirmed only</p></div></article><article><span>✦</span><div><small>Shopy Agent</small><strong>{controls?.agent_enabled ? 'On' : 'Off'}</strong><p>{controls?.category_allowlist.length ? `${controls.category_allowlist.length} allowed categories` : 'All catalogue categories'}</p></div></article><article><span>◇</span><div><small>Member since</small><strong>{formatDate(profile.created_at)}</strong><p>Database-backed account</p></div></article></div>
            <form className="profile-details-form" onSubmit={saveProfile}><div className="panel-subheading"><div><span>PERSONAL DETAILS</span><h3>Account information</h3></div><button type="submit" disabled={profileSaving || displayName.trim() === profile.display_name}>{profileSaving ? 'Saving…' : 'Save changes'}</button></div><div className="details-grid"><label><span>Display name</span><input value={displayName} minLength={2} maxLength={120} onChange={(event) => setDisplayName(event.target.value)} /></label><label><span>Email address</span><input value={profile.email} disabled /><small>Email changes require a verified workflow.</small></label><label><span>Account role</span><input value={profile.role === 'buyer' ? 'Shopper' : 'Merchant administrator'} disabled /></label><label><span>Email status</span><input value={profile.email_verified ? 'Verified' : 'Not verified'} disabled /></label></div></form>
          </section> : null}

          {activeTab === 'orders' ? <section className="account-panel history-panel"><header><div><span>ORDER HISTORY</span><h2>Your orders</h2><p>Only checkout records written by the signed purchase workflow.</p></div><b>{orders?.items.length ?? 0} orders</b></header>{orders?.items.length ? <div className="history-list">{orders.items.map((order) => <article key={order.order_id}><span>Order {order.order_id.slice(0, 8)}</span><strong>{formatPrice(order.amount_paise)}</strong><div className="history-meta"><span className={`status-badge status-${order.status.toLowerCase()}`}>{order.status}</span><small>· {formatDate(order.created_at)}</small></div></article>)}</div> : <HistoryEmpty kind="order" title="No authoritative orders yet" reason={orders?.reason ?? 'Loading order history…'} />}</section> : null}

          {activeTab === 'transactions' ? <section className="account-panel history-panel"><header><div><span>PAYMENT LEDGER</span><h2>Transactions</h2><p>Verified payment events only—never cart estimates.</p></div><b>{transactions?.items.length ?? 0} transactions</b></header>{transactions?.items.length ? <div className="history-list">{transactions.items.map((transaction) => <article key={transaction.transaction_id}><span>Payment · {transaction.transaction_id.slice(0, 8)}</span><strong>{formatPrice(transaction.amount_paise)}</strong><div className="history-meta"><span className={`status-badge status-${transaction.status.toLowerCase()}`}>{transaction.status}</span><small>· {formatDate(transaction.created_at)}</small></div></article>)}</div> : <HistoryEmpty kind="transaction" title="No verified transactions yet" reason={transactions?.reason ?? 'Loading transaction history…'} />}</section> : null}

          {activeTab === 'agent' ? <section className="account-panel agent-control-panel">
            <header><div><span>SHOPY AGENT POLICY</span><h2>Agent controls</h2><p>Set hard server-side limits applied before the live catalogue is searched.</p></div><label className="master-switch"><input type="checkbox" checked={draftControls?.agent_enabled ?? false} onChange={(event) => setDraftControls((current) => current ? { ...current, agent_enabled: event.target.checked } : current)} /><span /><b>{draftControls?.agent_enabled ? 'Agent on' : 'Agent off'}</b></label></header>
            {draftControls ? <form onSubmit={saveControls}>
              <div className="control-section"><div className="control-section-title"><span>01</span><div><h3>Recommendation boundary</h3><p>These values actively constrain authenticated Shopy Agent results.</p></div></div><div className="control-grid"><MoneyInput label="Recommendation ceiling" hint="Never recommend products above this price." value={draftControls.recommendation_price_ceiling_paise} onChange={(value) => setDraftControls({ ...draftControls, recommendation_price_ceiling_paise: value })} /><label className="control-field"><span>Maximum recommendations</span><div className="range-value"><input type="range" min="1" max="8" value={draftControls.max_recommendations} onChange={(event) => setDraftControls({ ...draftControls, max_recommendations: Number(event.target.value) })} /><b>{draftControls.max_recommendations}</b></div><small>Maximum product cards returned per request.</small></label></div></div>
              <div className="control-section"><div className="control-section-title"><span>02</span><div><h3>Spending policy</h3><p>Saved now for policy enforcement; payment authority remains inactive.</p></div></div><div className="control-grid two-by-two"><MoneyInput label="Per-purchase limit" hint="Also caps recommended product price." value={draftControls.per_purchase_limit_paise} onChange={(value) => setDraftControls({ ...draftControls, per_purchase_limit_paise: value })} /><MoneyInput label="Approval required above" hint="Future purchases above this need your approval." value={draftControls.approval_required_above_paise} onChange={(value) => setDraftControls({ ...draftControls, approval_required_above_paise: value })} /><MoneyInput label="Daily spending limit" hint="Maximum future authorized spend per day." value={draftControls.daily_spend_limit_paise} onChange={(value) => setDraftControls({ ...draftControls, daily_spend_limit_paise: value })} /><MoneyInput label="Monthly spending limit" hint="Maximum future authorized spend per month." value={draftControls.monthly_spend_limit_paise} onChange={(value) => setDraftControls({ ...draftControls, monthly_spend_limit_paise: value })} /></div><div className="authority-warning"><span>!</span><div><strong>Autonomous payment is not active</strong><p>{controls?.purchase_authority_notice ?? 'A saved preference is not permission to charge a payment method.'}</p></div><button type="button" disabled>Enable auto-pay</button></div></div>
              <div className="control-section"><div className="control-section-title"><span>03</span><div><h3>Categories & behavior</h3><p>Leave every category unselected to allow the complete catalogue.</p></div></div><div className="category-policy">{categoryChoices.map((category) => { const selected = draftControls.category_allowlist.includes(category.id); return <button type="button" key={category.id} className={selected ? 'selected' : ''} onClick={() => setDraftControls({ ...draftControls, category_allowlist: selected ? draftControls.category_allowlist.filter((value) => value !== category.id) : [...draftControls.category_allowlist, category.id] })}><span>{category.glyph}</span><strong>{category.label}</strong><i>{selected ? '✓' : '+'}</i></button> })}</div><div className="behavior-row"><label><input type="checkbox" checked={draftControls.allow_substitutions} onChange={(event) => setDraftControls({ ...draftControls, allow_substitutions: event.target.checked })} /><span><strong>Allow substitutions</strong><small>Let future workflows consider another eligible model.</small></span></label><label><span><strong>Maximum replans</strong><small>Bound future candidate retries from 0 to 10.</small></span><select value={draftControls.max_replans} onChange={(event) => setDraftControls({ ...draftControls, max_replans: Number(event.target.value) })}>{Array.from({ length: 11 }, (_, value) => <option value={value} key={value}>{value}</option>)}</select></label></div></div>
              <div className="control-savebar"><div><span>Policy version {controls?.version ?? 1}</span><small>Limits are persisted to your Shopy account.</small></div><button type="submit" disabled={saveState === 'saving'}>{saveState === 'saving' ? 'Saving policy…' : saveState === 'saved' ? 'Saved ✓' : 'Save agent controls'}</button></div>
            </form> : <div className="panel-loading">Loading saved agent policy…</div>}
          </section> : null}

          {activeTab === 'security' && profile ? <section className="account-panel security-panel"><header><div><span>LOGIN & SECURITY</span><h2>Account protection</h2><p>Review your current account and provider readiness.</p></div></header><div className="security-list"><article><div className="security-glyph safe">◇</div><div><strong>Password protection</strong><p>Your password is stored as an Argon2id hash. The original value cannot be read back.</p></div><span>Protected</span></article><article><div className="security-glyph safe">↗</div><div><strong>Current session</strong><p>Signed in with a revocable HttpOnly cookie and CSRF protection. Last login: {formatDate(profile.last_login_at)}.</p></div><span>Active</span></article><article><div className="security-glyph pending">@</div><div><strong>Email verification</strong><p>{profile.email_verified ? 'Your email address is verified.' : 'Email delivery is not connected, so Shopy does not pretend this address is verified.'}</p></div><span>{profile.email_verified ? 'Verified' : 'Pending'}</span></article><article><div className={health?.razorpay.status === 'configured' ? 'security-glyph safe' : 'security-glyph pending'}>₹</div><div><strong>Payment provider</strong><p>Gateway test configuration is separate from permission to make a purchase.</p></div><span>{health?.razorpay.status === 'configured' ? 'Test configured' : 'Not configured'}</span></article></div><button className="security-signout" type="button" onClick={signOut}>Sign out this session</button></section> : null}
        </div>
      </section>
    </main>
  )
}

export default AccountCenter

import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import logoPng from './assets/logo.png'
import AgentRunHistory from './agent/AgentRunHistory'
import {
  ApiError,
  clearAgentHistory,
  createAddress,
  deleteAddress,
  fetchAccountProfile,
  fetchAddresses,
  fetchAgentControls,
  fetchOrderHistory,
  fetchTransactionHistory,
  loginAccount,
  logoutAccount,
  signupAccount,
  updateAccountProfile,
  updateAddress,
  updateAgentControls,
} from './api'
import type {
  AccountProfile,
  AgentControls,
  AgentControlsUpdate,
  DeliveryAddress,
  DeliveryAddressInput,
  HealthStatus,
  OrderHistoryResponse,
  TransactionHistoryResponse,
} from './types'

type AccountTab = 'auth' | 'overview' | 'addresses' | 'orders' | 'transactions' | 'audit' | 'agent' | 'security'
type AuthMode = 'login' | 'signup'

type SessionState = 'loading' | 'guest' | 'authenticated'

const emptyAddress: DeliveryAddressInput = { full_name: '', phone: '', line1: '', line2: null, landmark: null, city: '', state: '', postal_code: '', is_default: false }

interface AccountCenterProps {
  health: HealthStatus | null
  /** Lets the rest of the app react to sign-in and sign-out. */
  onSession?: (profile: AccountProfile | null) => void
  onAgentEnabledChange?: (enabled: boolean) => void
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
    category_allowlist: [],
    max_recommendations: controls.max_recommendations,
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
    </section>
  )
}

function HistoryEmpty({ title, reason, kind }: { title: string; reason: string; kind: 'order' | 'transaction' }) {
  const orderSvg = <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M6 2L3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z" /><line x1="3" y1="6" x2="21" y2="6" /><path d="M16 10a4 4 0 0 1-8 0" /></svg>
  const txSvg = <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><rect x="2" y="5" width="20" height="14" rx="2" /><line x1="2" y1="10" x2="22" y2="10" /></svg>

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

function AccountCenter({ health, onSession, onAgentEnabledChange }: AccountCenterProps) {
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
  const [addresses, setAddresses] = useState<DeliveryAddress[]>([])
  const [addressDraft, setAddressDraft] = useState<DeliveryAddressInput>(emptyAddress)
  const [editingAddressId, setEditingAddressId] = useState<string | null>(null)
  const [addressFormOpen, setAddressFormOpen] = useState(false)
  const [addressBusy, setAddressBusy] = useState(false)
  const [openOrderId, setOpenOrderId] = useState<string | null>(null)
  const [openTransactionId, setOpenTransactionId] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    fetchAccountProfile(controller.signal)
      .then((account) => {
        setProfile(account)
        setDisplayName(account.display_name)
        setSessionState('authenticated')
        onSession?.(account)
        setActiveTab((current) => current === 'auth' ? 'overview' : current)
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        // Do not show 'Failed to fetch' error banner to the user just because they are not logged in or API is offline
        setSessionState('guest')
        onSession?.(null)
        setActiveTab('auth')
      })
    return () => controller.abort()
  }, [onSession])

  useEffect(() => {
    if (sessionState !== 'authenticated' || profile === null) return
    const controller = new AbortController()
    setDataError(null)
    Promise.all([
      fetchAgentControls(controller.signal),
      fetchOrderHistory(controller.signal),
      fetchTransactionHistory(controller.signal),
      fetchAddresses(controller.signal),
    ])
      .then(([savedControls, orderHistory, transactionHistory, savedAddresses]) => {
        setControls(savedControls)
        setDraftControls(controlsPayload(savedControls))
        onAgentEnabledChange?.(savedControls.agent_enabled)
        setOrders(orderHistory)
        setTransactions(transactionHistory)
        setAddresses(savedAddresses.items)
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
      onAgentEnabledChange?.(saved.agent_enabled)
      setSaveState('saved')
      window.setTimeout(() => setSaveState('idle'), 1800)
    } catch (error) {
      setDataError(error instanceof Error ? error.message : 'Could not save agent controls.')
      setSaveState('idle')
    }
  }

  async function clearHistory() {
    if (!window.confirm('Clear every saved Agent conversation? Payment and audit records will remain.')) return
    setSaveState('saving')
    setDataError(null)
    try {
      await clearAgentHistory()
      setSaveState('saved')
      window.setTimeout(() => setSaveState('idle'), 1800)
    } catch (error) {
      setDataError(error instanceof Error ? error.message : 'Could not clear Agent history.')
      setSaveState('idle')
    }
  }

  function startAddressForm(address?: DeliveryAddress) {
    setEditingAddressId(address?.id ?? null)
    setAddressDraft(address ? { full_name: address.full_name, phone: address.phone, line1: address.line1, line2: address.line2, landmark: address.landmark, city: address.city, state: address.state, postal_code: address.postal_code, is_default: address.is_default } : emptyAddress)
    setAddressFormOpen(true)
  }

  async function saveAddress(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setAddressBusy(true)
    setDataError(null)
    try {
      const saved = editingAddressId ? await updateAddress(editingAddressId, addressDraft) : await createAddress(addressDraft)
      setAddresses((items) => editingAddressId ? items.map((item) => item.id === saved.id ? saved : item).sort((a, b) => Number(b.is_default) - Number(a.is_default)) : [saved, ...items.map((item) => saved.is_default ? { ...item, is_default: false } : item)])
      setAddressFormOpen(false)
      setEditingAddressId(null)
      setAddressDraft(emptyAddress)
    } catch (error) {
      setDataError(error instanceof Error ? error.message : 'Could not save address.')
    } finally {
      setAddressBusy(false)
    }
  }

  async function removeAddress(addressId: string) {
    if (!window.confirm('Remove this saved address? Existing orders keep their delivery snapshot.')) return
    setAddressBusy(true)
    setDataError(null)
    try { await deleteAddress(addressId); setAddresses((items) => items.filter((item) => item.id !== addressId)) }
    catch (error) { setDataError(error instanceof Error ? error.message : 'Could not remove address.') }
    finally { setAddressBusy(false) }
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
      setAddresses([])
      setSessionState('guest')
      onSession?.(null)
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
    { id: 'addresses', label: 'Saved addresses', glyph: '⌂' },
    { id: 'orders', label: 'Your orders', glyph: '▤' },
    { id: 'transactions', label: 'Transactions', glyph: '↔' },
    { id: 'audit', label: 'Payment audit', glyph: '⌁' },
    { id: 'agent', label: 'Agent controls', glyph: '✦' },
    { id: 'security', label: 'Login & security', glyph: '◇' },
  ]

  return (
    <main className="account-page">
      <section className="account-hero">
        <div>
          <span className="section-label">{sessionState === 'guest' ? 'YOUR SHOPPING SPACE' : 'YOUR SHOPY ACCOUNT'}</span>
          <h1>{sessionState === 'authenticated' && profile ? <>Hello, {profile.display_name.split(' ')[0]}.</> : <>One account.<br /><em>Your rules.</em></>}</h1>
          {sessionState === 'authenticated' ? <p>Manage account details, real purchase history, and exactly how much authority your shopping agent can use.</p> : null}
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
              onSession?.(account)
              setActiveTab('overview')
            }} />
          ) : null}

          {activeTab === 'overview' && profile ? <section className="account-panel overview-panel">
            <header><div><span>ACCOUNT OVERVIEW</span><h2>Your details</h2><p>Identity data stored in Shopy's account database.</p></div><b className={profile.is_active ? 'account-state active' : 'account-state'}>{profile.is_active ? 'Active' : 'Disabled'}</b></header>
            <div className="overview-metrics"><article><span>▤</span><div><small>Orders</small><strong>{orders?.items.length ?? '—'}</strong><p>Provider-confirmed only</p></div></article><article><span>✦</span><div><small>Shopy Agent</small><strong>{controls?.agent_enabled ? 'On' : 'Off'}</strong><p>Recommendation and direct-buy intent</p></div></article><article><span>◇</span><div><small>Member since</small><strong>{formatDate(profile.created_at)}</strong><p>Database-backed account</p></div></article></div>
            <form className="profile-details-form" onSubmit={saveProfile}><div className="panel-subheading"><div><span>PERSONAL DETAILS</span><h3>Account information</h3></div><button type="submit" disabled={profileSaving || displayName.trim() === profile.display_name}>{profileSaving ? 'Saving…' : 'Save changes'}</button></div><div className="details-grid"><label><span>Display name</span><input value={displayName} minLength={2} maxLength={120} onChange={(event) => setDisplayName(event.target.value)} /></label><label><span>Email address</span><input value={profile.email} disabled /><small>Email changes require a verified workflow.</small></label><label><span>Account role</span><input value={profile.role === 'buyer' ? 'Shopper' : 'Merchant administrator'} disabled /></label><label><span>Email status</span><input value={profile.email_verified ? 'Verified' : 'Not verified'} disabled /></label></div></form>
          </section> : null}

          {activeTab === 'addresses' ? <section className="account-panel saved-address-panel"><header><div><span>DELIVERY DETAILS</span><h2>Saved addresses</h2><p>Manage the addresses available during cart and Agent checkout.</p></div><button className="address-add-button" type="button" onClick={() => startAddressForm()}>{addressFormOpen && !editingAddressId ? 'New address open' : '+ Add address'}</button></header>{addressFormOpen ? <form className="profile-address-form" onSubmit={saveAddress}><div className="address-form-grid">{(['full_name', 'phone', 'line1', 'city', 'state', 'postal_code'] as const).map((field) => <label key={field}><span>{field.replaceAll('_', ' ')}</span><input required value={addressDraft[field]} onChange={(event) => setAddressDraft((current) => ({ ...current, [field]: event.target.value }))}/></label>)}<label><span>Address line 2</span><input value={addressDraft.line2 ?? ''} onChange={(event) => setAddressDraft((current) => ({ ...current, line2: event.target.value || null }))}/></label><label><span>Landmark</span><input value={addressDraft.landmark ?? ''} onChange={(event) => setAddressDraft((current) => ({ ...current, landmark: event.target.value || null }))}/></label></div><label className="address-default-check"><input type="checkbox" checked={addressDraft.is_default} onChange={(event) => setAddressDraft((current) => ({ ...current, is_default: event.target.checked }))}/><span>Use as my default delivery address</span></label><div className="address-form-actions"><button type="button" onClick={() => { setAddressFormOpen(false); setEditingAddressId(null); setAddressDraft(emptyAddress) }}>Cancel</button><button type="submit" disabled={addressBusy}>{addressBusy ? 'Saving…' : editingAddressId ? 'Update address' : 'Save address'}</button></div></form> : null}<div className="profile-address-list">{addresses.map((address) => <article key={address.id}><div><span>{address.is_default ? 'DEFAULT ADDRESS' : 'SAVED ADDRESS'}</span><h3>{address.full_name}</h3><p>{address.line1}{address.line2 ? `, ${address.line2}` : ''}<br/>{address.city}, {address.state} {address.postal_code}<br/>{address.phone}</p></div><div><button type="button" onClick={() => startAddressForm(address)}>Edit</button><button type="button" onClick={() => void removeAddress(address.id)} disabled={addressBusy}>Remove</button></div></article>)}{addresses.length === 0 && !addressFormOpen ? <p className="address-empty">No saved addresses yet. Add one to speed up checkout.</p> : null}</div></section> : null}

          {activeTab === 'orders' ? <section className="account-panel history-panel"><header><div><span>ORDER HISTORY</span><h2>Your orders</h2><p>Open an order to see the product and provider details.</p></div><b>{orders?.items.length ?? 0} orders</b></header>{orders?.items.length ? <div className="history-list">{orders.items.map((order) => <article key={order.order_id}><button className="history-summary" type="button" aria-expanded={openOrderId === order.order_id} onClick={() => setOpenOrderId((current) => current === order.order_id ? null : order.order_id)}><span><strong>{order.product_title}</strong><small>{order.product_brand} · {order.product_model} · Qty {order.quantity}</small></span><strong>{formatPrice(order.amount_paise)}</strong><div className="history-meta"><span className={`status-badge status-${order.status.toLowerCase()}`}>{order.status}</span><small>{formatDate(order.created_at)}</small></div><i>{openOrderId === order.order_id ? '−' : '+'}</i></button>{openOrderId === order.order_id ? <dl className="history-detail"><div><dt>Product</dt><dd>{order.product_title}</dd></div><div><dt>SKU</dt><dd>{order.product_sku}</dd></div><div><dt>Razorpay order</dt><dd>{order.provider_order_id}</dd></div><div><dt>Amount</dt><dd>{formatPrice(order.amount_paise)}</dd></div><div><dt>Attempts</dt><dd>{order.attempts}</dd></div><div><dt>Updated</dt><dd>{formatDate(order.updated_at)}</dd></div></dl> : null}</article>)}</div> : <HistoryEmpty kind="order" title="No authoritative orders yet" reason={orders?.reason ?? 'Loading order history…'} />}</section> : null}

          {activeTab === 'transactions' ? <section className="account-panel history-panel"><header><div><span>PAYMENT LEDGER</span><h2>Transactions</h2><p>Open a transaction to see its product and payment details.</p></div><b>{transactions?.items.length ?? 0} transactions</b></header>{transactions?.items.length ? <div className="history-list">{transactions.items.map((transaction) => <article key={transaction.transaction_id}><button className="history-summary" type="button" aria-expanded={openTransactionId === transaction.transaction_id} onClick={() => setOpenTransactionId((current) => current === transaction.transaction_id ? null : transaction.transaction_id)}><span><strong>{transaction.product_title}</strong><small>{transaction.product_brand} · {transaction.product_model} · Qty {transaction.quantity}</small></span><strong>{formatPrice(transaction.amount_paise)}</strong><div className="history-meta"><span className={`status-badge status-${transaction.status.toLowerCase()}`}>{transaction.status}</span><small>{formatDate(transaction.created_at)}</small></div><i>{openTransactionId === transaction.transaction_id ? '−' : '+'}</i></button>{openTransactionId === transaction.transaction_id ? <dl className="history-detail"><div><dt>Product</dt><dd>{transaction.product_title}</dd></div><div><dt>Payment ID</dt><dd>{transaction.provider_payment_id}</dd></div><div><dt>Razorpay order</dt><dd>{transaction.provider_order_id}</dd></div><div><dt>Method</dt><dd>{transaction.payment_method ?? 'Not reported'}</dd></div><div><dt>Captured</dt><dd>{transaction.captured ? 'Yes' : 'No'}</dd></div><div><dt>Amount</dt><dd>{formatPrice(transaction.amount_paise)}</dd></div>{transaction.error_description ? <div><dt>Failure</dt><dd>{transaction.error_description}</dd></div> : null}</dl> : null}</article>)}</div> : <HistoryEmpty kind="transaction" title="No verified transactions yet" reason={transactions?.reason ?? 'Loading transaction history…'} />}</section> : null}

          {activeTab === 'audit' ? <AgentRunHistory /> : null}

          {activeTab === 'agent' ? <section className="account-panel agent-control-panel">
            <header><div><span>SHOPY AGENT</span><h2>Agent controls</h2><p>Keep the Agent simple: turn it on, set one purchase limit, and choose categories.</p></div><label className="master-switch"><input type="checkbox" checked={draftControls?.agent_enabled ?? false} onChange={(event) => setDraftControls((current) => current ? { ...current, agent_enabled: event.target.checked } : current)} /><span /><b>{draftControls?.agent_enabled ? 'Agent on' : 'Agent off'}</b></label></header>
            {draftControls ? <form onSubmit={saveControls}>
              <div className="control-section"><div className="control-section-title"><span>01</span><div><h3>Purchase limit</h3><p>The maximum amount allowed for one direct Agent purchase.</p></div></div><div className="control-grid"><MoneyInput label="Maximum per purchase" hint="A direct Agent purchase above this amount is blocked before Razorpay opens." value={draftControls.per_purchase_limit_paise} onChange={(value) => setDraftControls({ ...draftControls, per_purchase_limit_paise: value })} /></div></div>
              <div className="control-section"><div className="control-section-title"><span>02</span><div><h3>Agent history</h3><p>Clear saved chats and searches. Payment orders and audit records remain protected.</p></div></div><button className="security-signout" type="button" onClick={clearHistory} disabled={saveState === 'saving'}>{saveState === 'saving' ? 'Clearing…' : 'Clear all Agent history'}</button></div>
              <div className="control-savebar"><div><span>Policy version {controls?.version ?? 1}</span><small>Every direct purchase still requires your saved address and Razorpay confirmation.</small></div><button type="submit" disabled={saveState === 'saving'}>{saveState === 'saving' ? 'Saving…' : saveState === 'saved' ? 'Saved ✓' : 'Save controls'}</button></div>
            </form> : <div className="panel-loading">Loading Agent controls…</div>}
          </section> : null}

          {activeTab === 'security' && profile ? <section className="account-panel security-panel"><header><div><span>LOGIN & SECURITY</span><h2>Account protection</h2><p>Review your current account and provider readiness.</p></div></header><div className="security-list"><article><div className="security-glyph safe">◇</div><div><strong>Password protection</strong><p>Your password is stored as an Argon2id hash. The original value cannot be read back.</p></div><span>Protected</span></article><article><div className="security-glyph safe">↗</div><div><strong>Current session</strong><p>Signed in with a revocable HttpOnly cookie and CSRF protection. Last login: {formatDate(profile.last_login_at)}.</p></div><span>Active</span></article><article><div className="security-glyph pending">@</div><div><strong>Email verification</strong><p>{profile.email_verified ? 'Your email address is verified.' : 'Email delivery is not connected, so Shopy does not pretend this address is verified.'}</p></div><span>{profile.email_verified ? 'Verified' : 'Pending'}</span></article><article><div className={health?.razorpay.status === 'configured' ? 'security-glyph safe' : 'security-glyph pending'}>₹</div><div><strong>Payment provider</strong><p>Razorpay test configuration is separate from permission to make a purchase.</p></div><span>{health?.razorpay.status === 'configured' ? 'Test configured' : 'Not configured'}</span></article></div><button className="security-signout" type="button" onClick={signOut}>Sign out this session</button></section> : null}
        </div>
      </section>
    </main>
  )
}

export default AccountCenter

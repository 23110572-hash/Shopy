import { useEffect, useState } from 'react'
import {
  ApiError,
  confirmCheckoutPayment,
  createAddress,
  createCheckoutOrder,
  decidePostPurchaseCrossSell,
  fetchAddresses,
  fetchCheckoutStatus,
  reconcileCheckoutPayment,
} from '../api'
import {
  CheckoutDismissedError,
  fromCheckoutSession,
  loadRazorpayCheckout,
  openRazorpayCheckout,
} from '../razorpay'
import type {
  DeliveryAddress,
  DeliveryAddressInput,
  PurchaseProposal,
  PurchaseRunStatus,
} from '../types'

const empty: DeliveryAddressInput = {
  full_name: '',
  phone: '',
  line1: '',
  line2: null,
  landmark: null,
  city: '',
  state: '',
  postal_code: '',
  is_default: false,
}
const money = (paise: number) => new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
}).format(paise / 100)

type AgentCheckoutProps = {
  proposal: PurchaseProposal
  signedIn: boolean
  onSignIn: () => void
  onRunChange: () => void
  onProposalCreated: (proposal: PurchaseProposal) => void
}

export default function AgentCheckout({
  proposal,
  signedIn,
  onSignIn,
  onRunChange,
  onProposalCreated,
}: AgentCheckoutProps) {
  const [addresses, setAddresses] = useState<DeliveryAddress[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState<DeliveryAddressInput>(empty)
  const [status, setStatus] = useState<PurchaseRunStatus | null>(null)
  const [runId, setRunId] = useState<string | null>(proposal.run_id)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [offerMessage, setOfferMessage] = useState<string | null>(null)

  useEffect(() => {
    setAddresses([])
    setSelected(null)
    setConfirmed(false)
    setAdding(false)
    setDraft(empty)
    setStatus(null)
    setRunId(proposal.run_id)
    setBusy(false)
    setError(null)
    setOfferMessage(null)
    if (!signedIn) return

    const controller = new AbortController()
    void fetchAddresses(controller.signal).then((result) => {
      setAddresses(result.items)
      setSelected(
        (result.items.find((address) => address.is_default) ?? result.items[0])?.id ?? null,
      )
    }).catch((reason: unknown) => {
      if (!(reason instanceof DOMException && reason.name === 'AbortError')) {
        setError(reason instanceof Error ? reason.message : 'Addresses are unavailable.')
      }
    })
    void fetchCheckoutStatus(proposal.run_id, controller.signal).then((result) => {
      setStatus(result)
      if (result.post_purchase_proposal) {
        onProposalCreated(result.post_purchase_proposal)
      }
    }).catch(
      (reason: unknown) => {
        if (!(reason instanceof DOMException && reason.name === 'AbortError')) {
          setError(reason instanceof Error ? reason.message : 'The proposal status is unavailable.')
        }
      },
    )
    return () => controller.abort()
  }, [proposal.proposal_id, proposal.run_id, signedIn])

  async function saveAddress(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const saved = await createAddress(draft)
      setAddresses((items) => [saved, ...items])
      setSelected(saved.id)
      setConfirmed(false)
      setAdding(false)
      setDraft(empty)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Address could not be saved.')
    } finally {
      setBusy(false)
    }
  }

  async function pay() {
    if (!selected || !confirmed) return
    setBusy(true)
    setError(null)
    let activeRun = runId
    try {
      const [session] = await Promise.all([
        createCheckoutOrder(proposal.proposal_id, selected),
        loadRazorpayCheckout(),
      ])
      activeRun = session.run_id
      setRunId(activeRun)
      const callback = await openRazorpayCheckout(fromCheckoutSession(session))
      const result = await confirmCheckoutPayment(activeRun, callback)
      setStatus(result)
      onRunChange()
    } catch (reason) {
      if (reason instanceof CheckoutDismissedError && activeRun) {
        try {
          setStatus(await reconcileCheckoutPayment(activeRun))
          onRunChange()
          return
        } catch {
          setError('Checkout closed. Reconcile this same run before trying anything else.')
          return
        }
      }
      if (activeRun) {
        try {
          setStatus(await fetchCheckoutStatus(activeRun))
        } catch {
          // Preserve the original checkout error.
        }
      }
      setError(
        reason instanceof ApiError
          ? reason.message
          : reason instanceof Error
            ? reason.message
            : 'Agent checkout failed.',
      )
    } finally {
      setBusy(false)
    }
  }

  async function reconcile() {
    if (!runId) return
    setBusy(true)
    setError(null)
    try {
      setStatus(await reconcileCheckoutPayment(runId))
      onRunChange()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Status is unavailable.')
    } finally {
      setBusy(false)
    }
  }

  async function decideOffer(decision: 'ACCEPT' | 'DECLINE') {
    const offer = status?.post_purchase_offer
    if (!offer || !runId) return
    setBusy(true)
    setError(null)
    setOfferMessage(null)
    try {
      const result = await decidePostPurchaseCrossSell(runId, {
        decision,
        product_id: offer.product.id,
        product_version: offer.product_version,
      })
      setStatus((value) => value ? { ...value, post_purchase_offer: null } : value)
      setOfferMessage(result.message)
      onRunChange()
      if (decision === 'ACCEPT') {
        if (!result.purchase_proposal) {
          throw new Error('The add-on was accepted but its separate proposal is unavailable.')
        }
        onProposalCreated(result.purchase_proposal)
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The optional add-on decision failed.')
    } finally {
      setBusy(false)
    }
  }

  if (!signedIn) {
    return <div className="agent-checkout-gate">
      <p>Sign in to choose an address and confirm this governed Razorpay purchase.</p>
      <button type="button" onClick={onSignIn}>Sign in to continue</button>
    </div>
  }

  const captured = status?.state === 'CAPTURED'
  const offer = captured ? status?.post_purchase_offer : null
  return <section className="agent-checkout-card">
    <div className="agent-panel-title"><span>Delivery & payment</span><b>Razorpay only</b></div>
    <h3>{proposal.product.title}</h3>
    <strong className="agent-price">{money(proposal.amount_paise)}</strong>
    <div className="policy-list">
      {proposal.policy_checks?.map((check) => <p
        key={check.code}
        className={check.outcome === 'ALLOWED' ? 'allowed' : 'blocked'}
      >
        <b>{check.outcome === 'ALLOWED' ? '✓' : '!'}</b>
        <span>{check.explanation}</span>
      </p>)}
    </div>
    {captured ? <div className="agent-payment-success">
      <strong>Payment captured</strong>
      <p>{status.message}</p>
      <small>{status.fulfillment_order_number ?? status.order_id}</small>
    </div> : <>
      <label className="agent-field">
        <span>Saved delivery address</span>
        <select
          value={selected ?? ''}
          onChange={(event) => {
            setSelected(event.target.value || null)
            setConfirmed(false)
          }}
        >
          <option value="">Select an address</option>
          {addresses.map((address) => <option key={address.id} value={address.id}>
            {address.full_name} — {address.city}, {address.postal_code}
          </option>)}
        </select>
      </label>
      <button
        className="agent-text-button"
        type="button"
        onClick={() => setAdding((value) => !value)}
      >
        {adding ? 'Cancel new address' : '+ Add delivery address'}
      </button>
      {adding ? <form className="agent-address-form" onSubmit={saveAddress}>
        {(['full_name', 'phone', 'line1', 'city', 'state', 'postal_code'] as const).map(
          (field) => <label key={field}>
            <span>{field.replaceAll('_', ' ')}</span>
            <input
              required
              value={draft[field]}
              onChange={(event) => setDraft((value) => ({
                ...value,
                [field]: event.target.value,
              }))}
            />
          </label>,
        )}
        <button disabled={busy}>Save address</button>
      </form> : null}
      <label className="agent-confirm-address">
        <input
          type="checkbox"
          checked={confirmed}
          onChange={(event) => setConfirmed(event.target.checked)}
          disabled={!selected}
        />
        <span>I confirm this saved address for this one agent order.</span>
      </label>
      <button
        className="agent-pay-button"
        type="button"
        onClick={pay}
        disabled={busy || !selected || !confirmed || !proposal.checkout_available}
      >
        {busy ? 'Working safely…' : `Confirm & pay ${money(proposal.amount_paise)}`}
      </button>
    </>}
    {offer ? <div className="post-purchase-offer">
      <span className="exact-badge">Optional add-on</span>
      <h4>{offer.product.title}</h4>
      <strong>{money(offer.product.offer_price_paise)}</strong>
      <p>{offer.prompt}</p>
      <div>
        <button type="button" onClick={() => void decideOffer('ACCEPT')} disabled={busy}>
          Yes, create separate checkout
        </button>
        <button type="button" onClick={() => void decideOffer('DECLINE')} disabled={busy}>
          No thanks
        </button>
      </div>
      <small>Nothing is charged now. You must confirm its address and Razorpay payment separately.</small>
    </div> : null}
    {offerMessage ? <p className="agent-offer-message">{offerMessage}</p> : null}
    {status && !captured ? <div className="agent-run-status">
      <p>{status.message}</p>
      {status.allowed_actions.includes('RECONCILE') || status.state === 'PAYMENT_UNKNOWN'
        ? <button type="button" onClick={reconcile} disabled={busy}>Reconcile same payment</button>
        : null}
    </div> : null}
    {error ? <p className="agent-inline-error">{error}</p> : null}
    <small>No COD and no autonomous payment. Razorpay confirmation is always yours.</small>
  </section>
}

import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import {
  ApiError,
  confirmOrderPayment,
  createAddress,
  deleteAddress,
  fetchAddresses,
  placeOrder,
  reconcileOrderPayment,
  updateAddress,
} from './api'
import {
  CheckoutDismissedError,
  fromOrderHandoff,
  loadRazorpayCheckout,
  openRazorpayCheckout,
} from './razorpay'
import type {
  CartItem,
  CustomerOrder,
  DeliveryAddress,
  DeliveryAddressInput,
  PaymentMethod,
} from './types'

function formatPrice(paise: number): string {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(paise / 100)
}

const emptyAddress: DeliveryAddressInput = {
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

function toInput(address: DeliveryAddress): DeliveryAddressInput {
  return {
    full_name: address.full_name,
    phone: address.phone,
    line1: address.line1,
    line2: address.line2,
    landmark: address.landmark,
    city: address.city,
    state: address.state,
    postal_code: address.postal_code,
    is_default: address.is_default,
  }
}

function addressLines(address: DeliveryAddress): string {
  return [address.line1, address.line2, address.landmark].filter(Boolean).join(', ')
}

function AddressForm({
  initial,
  busy,
  onCancel,
  onSubmit,
}: {
  initial: DeliveryAddressInput
  busy: boolean
  onCancel: (() => void) | null
  onSubmit: (value: DeliveryAddressInput) => void
}) {
  const [draft, setDraft] = useState<DeliveryAddressInput>(initial)

  function update<K extends keyof DeliveryAddressInput>(
    key: K,
    value: DeliveryAddressInput[K],
  ) {
    setDraft((current) => ({ ...current, [key]: value }))
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    onSubmit({
      ...draft,
      line2: draft.line2?.trim() ? draft.line2.trim() : null,
      landmark: draft.landmark?.trim() ? draft.landmark.trim() : null,
    })
  }

  return (
    <form className="address-form" onSubmit={submit}>
      <div className="address-grid">
        <label>
          <span>Full name</span>
          <input
            required
            minLength={2}
            maxLength={120}
            autoComplete="name"
            value={draft.full_name}
            onChange={(event) => update('full_name', event.target.value)}
            placeholder="Who receives this order"
          />
        </label>
        <label>
          <span>Mobile number</span>
          <input
            required
            inputMode="numeric"
            maxLength={13}
            autoComplete="tel-national"
            value={draft.phone}
            onChange={(event) => update('phone', event.target.value)}
            placeholder="10-digit number"
          />
        </label>
        <label className="address-span">
          <span>Flat, house, building, street</span>
          <input
            required
            minLength={4}
            maxLength={255}
            autoComplete="address-line1"
            value={draft.line1}
            onChange={(event) => update('line1', event.target.value)}
            placeholder="House / flat and street"
          />
        </label>
        <label className="address-span">
          <span>Area, colony (optional)</span>
          <input
            maxLength={255}
            autoComplete="address-line2"
            value={draft.line2 ?? ''}
            onChange={(event) => update('line2', event.target.value)}
            placeholder="Area or locality"
          />
        </label>
        <label className="address-span">
          <span>Landmark (optional)</span>
          <input
            maxLength={160}
            value={draft.landmark ?? ''}
            onChange={(event) => update('landmark', event.target.value)}
            placeholder="Nearby landmark"
          />
        </label>
        <label>
          <span>City</span>
          <input
            required
            minLength={2}
            maxLength={120}
            autoComplete="address-level2"
            value={draft.city}
            onChange={(event) => update('city', event.target.value)}
            placeholder="City"
          />
        </label>
        <label>
          <span>State</span>
          <input
            required
            minLength={2}
            maxLength={120}
            autoComplete="address-level1"
            value={draft.state}
            onChange={(event) => update('state', event.target.value)}
            placeholder="State"
          />
        </label>
        <label>
          <span>PIN code</span>
          <input
            required
            inputMode="numeric"
            maxLength={6}
            autoComplete="postal-code"
            value={draft.postal_code}
            onChange={(event) => update('postal_code', event.target.value)}
            placeholder="6-digit PIN"
          />
        </label>
        <label>
          <span>Country</span>
          <input value="India" disabled />
        </label>
      </div>
      <label className="address-default">
        <input
          type="checkbox"
          checked={draft.is_default}
          onChange={(event) => update('is_default', event.target.checked)}
        />
        <span>Use this as my default delivery address</span>
      </label>
      <div className="address-actions">
        <button type="submit" disabled={busy}>
          {busy ? 'Saving…' : 'Save address'}
        </button>
        {onCancel ? (
          <button type="button" className="ghost-button" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
        ) : null}
      </div>
    </form>
  )
}

interface CheckoutProps {
  cart: CartItem[]
  onOrderConfirmed: () => void
  onBack: () => void
  onSignIn: () => void
}

function Checkout({ cart, onOrderConfirmed, onBack, onSignIn }: CheckoutProps) {
  const [loading, setLoading] = useState(true)
  // Only set when the server actually rejects the session, never for other errors.
  const [sessionExpired, setSessionExpired] = useState(false)
  const [addresses, setAddresses] = useState<DeliveryAddress[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<DeliveryAddress | null>(null)
  const [method, setMethod] = useState<PaymentMethod>('COD')
  const [savingAddress, setSavingAddress] = useState(false)
  const [placing, setPlacing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [order, setOrder] = useState<CustomerOrder | null>(null)

  const subtotal = useMemo(
    () => cart.reduce((sum, item) => sum + item.product.offer_price_paise * item.quantity, 0),
    [cart],
  )
  const itemCount = useMemo(
    () => cart.reduce((sum, item) => sum + item.quantity, 0),
    [cart],
  )

  useEffect(() => {
    const controller = new AbortController()
    fetchAddresses(controller.signal)
      .then((result) => {
        setAddresses(result.items)
        const preferred = result.items.find((item) => item.is_default) ?? result.items[0]
        setSelectedId(preferred?.id ?? null)
        setFormOpen(result.items.length === 0)
      })
      .catch((requestError: unknown) => {
        if (requestError instanceof DOMException && requestError.name === 'AbortError') return
        if (requestError instanceof ApiError && requestError.status === 401) {
          setSessionExpired(true)
          return
        }
        // Any other failure is a real error; keep the form usable and surface it
        // instead of wrongly telling a signed-in shopper to sign in.
        setFormOpen(true)
        setError(
          requestError instanceof Error
            ? requestError.message
            : 'Saved addresses could not be loaded.',
        )
      })
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [])

  async function saveAddress(value: DeliveryAddressInput) {
    setSavingAddress(true)
    setError(null)
    try {
      const saved = editing
        ? await updateAddress(editing.id, value)
        : await createAddress(value)
      setAddresses((current) => {
        const others = current
          .filter((item) => item.id !== saved.id)
          .map((item) => (saved.is_default ? { ...item, is_default: false } : item))
        return [saved, ...others]
      })
      setSelectedId(saved.id)
      setFormOpen(false)
      setEditing(null)
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'The address could not be saved.')
    } finally {
      setSavingAddress(false)
    }
  }

  async function removeAddress(addressId: string) {
    setError(null)
    try {
      await deleteAddress(addressId)
      setAddresses((current) => current.filter((item) => item.id !== addressId))
      setSelectedId((current) => (current === addressId ? null : current))
    } catch (removeError) {
      setError(
        removeError instanceof Error ? removeError.message : 'The address could not be removed.',
      )
    }
  }

  function settle(placedOrder: CustomerOrder) {
    setOrder(placedOrder)
    if (placedOrder.status === 'CONFIRMED') onOrderConfirmed()
  }

  async function submitOrder() {
    if (!selectedId || cart.length === 0) return
    setPlacing(true)
    setError(null)
    try {
      const result = await placeOrder({
        address_id: selectedId,
        payment_method: method,
        items: cart.map((item) => ({
          product_id: item.product.id,
          quantity: item.quantity,
        })),
      })

      if (!result.razorpay) {
        settle(result.order)
        return
      }

      await loadRazorpayCheckout()
      try {
        const callback = await openRazorpayCheckout(fromOrderHandoff(result.razorpay))
        settle(await confirmOrderPayment(result.order.id, callback))
      } catch (paymentError) {
        if (paymentError instanceof CheckoutDismissedError) {
          // The shopper closed Razorpay. Ask the server what actually happened.
          settle(await reconcileOrderPayment(result.order.id))
          return
        }
        throw paymentError
      }
    } catch (orderError) {
      if (orderError instanceof ApiError && orderError.status === 401) {
        setSessionExpired(true)
        return
      }
      setError(
        orderError instanceof Error ? orderError.message : 'The order could not be placed.',
      )
    } finally {
      setPlacing(false)
    }
  }

  async function refreshPayment() {
    if (!order) return
    setPlacing(true)
    setError(null)
    try {
      settle(await reconcileOrderPayment(order.id))
    } catch (refreshError) {
      setError(
        refreshError instanceof Error
          ? refreshError.message
          : 'The payment status is unavailable.',
      )
    } finally {
      setPlacing(false)
    }
  }

  if (loading) {
    return (
      <section className="checkout-panel">
        <div className="account-spinner" />
        <p>Opening checkout…</p>
      </section>
    )
  }

  if (sessionExpired) {
    return (
      <section className="checkout-panel checkout-gate">
        <span className="section-label">SESSION EXPIRED</span>
        <h2>Please sign in again</h2>
        <p>
          Your session is no longer valid, so this order was not placed. Sign in again and your
          cart will still be here.
        </p>
        <div className="checkout-gate-actions">
          <button type="button" className="primary-action" onClick={onSignIn}>
            Go to sign in →
          </button>
          <button type="button" className="ghost-button" onClick={onBack}>
            ← Back to cart
          </button>
        </div>
      </section>
    )
  }

  if (order) {
    const confirmed = order.status === 'CONFIRMED'
    const failed = order.status === 'PAYMENT_FAILED'
    return (
      <section className={confirmed ? 'checkout-panel order-done' : 'checkout-panel order-pending'}>
        <span className="section-label">
          {confirmed ? 'ORDER CONFIRMED' : failed ? 'PAYMENT FAILED' : 'AWAITING PAYMENT'}
        </span>
        <h2>{order.order_number}</h2>
        <p className="order-done-message">{order.message}</p>
        <dl className="order-facts">
          <div>
            <dt>Items</dt>
            <dd>{order.item_count}</dd>
          </div>
          <div>
            <dt>Total</dt>
            <dd>{formatPrice(order.total_paise)}</dd>
          </div>
          <div>
            <dt>Payment</dt>
            <dd>{order.payment_method === 'COD' ? 'Cash on delivery' : 'Razorpay (Test)'}</dd>
          </div>
          <div>
            <dt>Status</dt>
            <dd>{order.payment_status}</dd>
          </div>
        </dl>
        <div className="order-address">
          <strong>Delivering to</strong>
          <p>
            {order.shipping_address.full_name} · {order.shipping_address.phone}
            <br />
            {[order.shipping_address.line1, order.shipping_address.line2]
              .filter(Boolean)
              .join(', ')}
            <br />
            {order.shipping_address.city}, {order.shipping_address.state}{' '}
            {order.shipping_address.postal_code}
          </p>
        </div>
        {order.failure_reason ? <p className="checkout-error">{order.failure_reason}</p> : null}
        {error ? <p className="checkout-error">{error}</p> : null}
        <div className="checkout-gate-actions">
          {!confirmed ? (
            <button
              type="button"
              className="primary-action"
              onClick={refreshPayment}
              disabled={placing}
            >
              {placing ? 'Checking…' : 'Check payment status'}
            </button>
          ) : null}
          <button type="button" className="ghost-button" onClick={onBack}>
            {confirmed ? 'Continue shopping →' : '← Back to cart'}
          </button>
        </div>
      </section>
    )
  }

  const canPlace = selectedId !== null && cart.length > 0 && !placing

  return (
    <section className="checkout-layout">
      <div className="checkout-main">
        <section className="checkout-panel">
          <div className="checkout-step-head">
            <span className="step-badge">1</span>
            <div>
              <h2>Delivery address</h2>
              <p>Choose where this order should be delivered.</p>
            </div>
          </div>

          {addresses.length > 0 ? (
            <div className="address-list" role="radiogroup" aria-label="Saved delivery addresses">
              {addresses.map((address) => (
                <label
                  key={address.id}
                  className={selectedId === address.id ? 'address-card selected' : 'address-card'}
                >
                  <input
                    type="radio"
                    name="delivery-address"
                    checked={selectedId === address.id}
                    onChange={() => setSelectedId(address.id)}
                  />
                  <div className="address-body">
                    <strong>
                      {address.full_name}
                      {address.is_default ? <b className="default-chip">Default</b> : null}
                    </strong>
                    <p>{addressLines(address)}</p>
                    <p>
                      {address.city}, {address.state} {address.postal_code}
                    </p>
                    <small>Mobile {address.phone}</small>
                  </div>
                  <div className="address-card-actions">
                    <button
                      type="button"
                      onClick={() => {
                        setEditing(address)
                        setFormOpen(true)
                      }}
                    >
                      Edit
                    </button>
                    <button type="button" onClick={() => removeAddress(address.id)}>
                      Remove
                    </button>
                  </div>
                </label>
              ))}
            </div>
          ) : null}

          {formOpen ? (
            <AddressForm
              initial={editing ? toInput(editing) : emptyAddress}
              busy={savingAddress}
              onCancel={
                addresses.length > 0
                  ? () => {
                      setFormOpen(false)
                      setEditing(null)
                    }
                  : null
              }
              onSubmit={saveAddress}
            />
          ) : (
            <button
              type="button"
              className="ghost-button add-address"
              onClick={() => {
                setEditing(null)
                setFormOpen(true)
              }}
            >
              + Add a new address
            </button>
          )}
        </section>

        <section className="checkout-panel">
          <div className="checkout-step-head">
            <span className="step-badge">2</span>
            <div>
              <h2>Payment method</h2>
              <p>Pay now with Razorpay, or pay cash when the order arrives.</p>
            </div>
          </div>
          <div className="method-list" role="radiogroup" aria-label="Payment method">
            <label className={method === 'COD' ? 'method-card selected' : 'method-card'}>
              <input
                type="radio"
                name="payment-method"
                checked={method === 'COD'}
                onChange={() => setMethod('COD')}
              />
              <div>
                <strong>Cash on delivery</strong>
                <small>Pay the courier in cash when your order is delivered.</small>
              </div>
            </label>
            <label className={method === 'RAZORPAY' ? 'method-card selected' : 'method-card'}>
              <input
                type="radio"
                name="payment-method"
                checked={method === 'RAZORPAY'}
                onChange={() => setMethod('RAZORPAY')}
              />
              <div>
                <strong>Pay online</strong>
                <small>
                  UPI, cards, and netbanking.
                </small>
              </div>
            </label>
          </div>
        </section>
      </div>

      <aside className="checkout-summary">
        <span className="section-label">ORDER SUMMARY</span>
        <div className="summary-lines">
          {cart.map(({ product, quantity }) => (
            <div key={product.id}>
              <span>
                {product.title} × {quantity}
              </span>
              <b>{formatPrice(product.offer_price_paise * quantity)}</b>
            </div>
          ))}
        </div>
        <div className="summary-row">
          <span>Items ({itemCount})</span>
          <strong>{formatPrice(subtotal)}</strong>
        </div>
        <div className="summary-row">
          <span>Delivery</span>
          <strong>Free</strong>
        </div>
        <div className="summary-row summary-total">
          <span>Total payable</span>
          <strong>{formatPrice(subtotal)}</strong>
        </div>
        {error ? <p className="checkout-error">{error}</p> : null}
        {selectedId === null ? (
          <p className="checkout-hint">Add or select a delivery address to continue.</p>
        ) : null}
        <button
          type="button"
          className="place-order"
          onClick={submitOrder}
          disabled={!canPlace}
        >
          {placing
            ? 'Placing order…'
            : method === 'COD'
              ? `Place order · ${formatPrice(subtotal)}`
              : `Pay ${formatPrice(subtotal)} with Razorpay`}
        </button>
        <button type="button" className="continue-button" onClick={onBack}>
          ← Back to cart
        </button>
      </aside>
    </section>
  )
}

export default Checkout

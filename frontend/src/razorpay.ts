import type { CheckoutCallback, CheckoutSession } from './types'

const RAZORPAY_SCRIPT_URL = 'https://checkout.razorpay.com/v1/checkout.js'

interface RazorpayHandlerResponse {
  razorpay_payment_id?: unknown
  razorpay_order_id?: unknown
  razorpay_signature?: unknown
}

interface RazorpayFailureResponse {
  error?: { description?: unknown; reason?: unknown }
}

interface RazorpayOptions {
  key: string
  amount: number
  currency: string
  name: string
  description: string
  order_id: string
  prefill: { name: string; email: string }
  theme: { color: string }
  handler: (response: RazorpayHandlerResponse) => void
  modal: { ondismiss: () => void; escape: boolean }
}

interface RazorpayInstance {
  open: () => void
  on: (event: string, handler: (payload: RazorpayFailureResponse) => void) => void
}

declare global {
  interface Window {
    Razorpay?: new (options: RazorpayOptions) => RazorpayInstance
  }
}

let scriptPromise: Promise<void> | null = null

/** Load the hosted Razorpay Checkout script once and reuse it afterwards. */
export function loadRazorpayCheckout(): Promise<void> {
  if (window.Razorpay) return Promise.resolve()
  if (scriptPromise) return scriptPromise

  scriptPromise = new Promise<void>((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(
      `script[src="${RAZORPAY_SCRIPT_URL}"]`,
    )
    const script = existing ?? document.createElement('script')
    script.addEventListener('load', () => resolve(), { once: true })
    script.addEventListener(
      'error',
      () => {
        scriptPromise = null
        reject(new Error('Razorpay Checkout could not be loaded. Check your connection.'))
      },
      { once: true },
    )
    if (!existing) {
      script.src = RAZORPAY_SCRIPT_URL
      script.async = true
      document.head.appendChild(script)
    }
  })
  return scriptPromise
}

/** Raised when the shopper closes the Razorpay modal without paying. */
export class CheckoutDismissedError extends Error {
  constructor() {
    super('Checkout was closed before payment completed.')
    this.name = 'CheckoutDismissedError'
  }
}

function readProviderField(value: unknown, field: string): string {
  if (typeof value !== 'string' || value.trim() === '') {
    throw new Error(`Razorpay did not return ${field}.`)
  }
  return value.trim()
}

/**
 * Open Razorpay Standard Checkout for an existing server-created Order and
 * resolve with the raw provider callback. The signature is verified
 * server-side; nothing here is trusted as proof of payment.
 */
export function openRazorpayCheckout(session: CheckoutSession): Promise<CheckoutCallback> {
  return new Promise<CheckoutCallback>((resolve, reject) => {
    const Constructor = window.Razorpay
    if (!Constructor) {
      reject(new Error('Razorpay Checkout is unavailable.'))
      return
    }

    let settled = false
    const finish = (action: () => void) => {
      if (settled) return
      settled = true
      action()
    }

    const instance = new Constructor({
      key: session.key_id,
      amount: session.amount_paise,
      currency: session.currency,
      name: session.merchant_name,
      description: session.description,
      order_id: session.order_id,
      prefill: { name: session.prefill_name, email: session.prefill_email },
      theme: { color: '#5b3df5' },
      handler: (response) => {
        finish(() => {
          try {
            resolve({
              razorpay_payment_id: readProviderField(
                response.razorpay_payment_id,
                'a payment id',
              ),
              razorpay_order_id: readProviderField(response.razorpay_order_id, 'an order id'),
              razorpay_signature: readProviderField(
                response.razorpay_signature,
                'a signature',
              ),
            })
          } catch (error) {
            reject(error instanceof Error ? error : new Error('Razorpay response was invalid.'))
          }
        })
      },
      modal: {
        escape: true,
        ondismiss: () => finish(() => reject(new CheckoutDismissedError())),
      },
    })

    instance.on('payment.failed', (payload) => {
      const description = payload?.error?.description
      finish(() =>
        reject(
          new Error(
            typeof description === 'string' && description.trim() !== ''
              ? description
              : 'Razorpay reported that the payment failed.',
          ),
        ),
      )
    })

    instance.open()
  })
}

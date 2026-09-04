import { useEffect, useState } from 'react'
import { ApiError, fetchAgentRuns, fetchRunAudit } from '../api'
import type { AgentRunHistoryItem, AuditHistoryItem } from '../types'

const money = (paise: number | null) => paise === null ? '—' : new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(paise / 100)
const when = (value: string) => new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
const valueOrFallback = (value: string | null, fallback = '—') => value?.trim() || fallback

export default function AgentRunHistory({ refreshKey = 0 }: { refreshKey?: number }) {
  const [runs, setRuns] = useState<AgentRunHistoryItem[]>([])
  const [open, setOpen] = useState<string | null>(null)
  const [audit, setAudit] = useState<Record<string, AuditHistoryItem[]>>({})
  const [loading, setLoading] = useState(true)
  const [loadingAudit, setLoadingAudit] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    fetchAgentRuns(controller.signal).then((data) => setRuns(data.items)).catch((reason: unknown) => {
      if (reason instanceof DOMException && reason.name === 'AbortError') return
      if (reason instanceof ApiError && reason.status === 404) { setRuns([]); return }
      setError(reason instanceof Error ? reason.message : 'Payment audit is unavailable.')
    }).finally(() => setLoading(false))
    return () => controller.abort()
  }, [refreshKey])

  async function toggle(runId: string) {
    if (open === runId) { setOpen(null); return }
    setOpen(runId)
    if (audit[runId]) return
    setLoadingAudit(runId)
    setError(null)
    try {
      const result = await fetchRunAudit(runId)
      setAudit((current) => ({ ...current, [runId]: result.items }))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Audit timeline is unavailable.')
    } finally {
      setLoadingAudit((current) => current === runId ? null : current)
    }
  }

  return <section className="account-panel agent-history payment-audit-panel" aria-label="Payment audit timeline">
    <header className="payment-audit-heading">
      <div><span>PAYMENT GOVERNANCE</span><h2>Payment audit timeline</h2></div>
      <b>{runs.length} {runs.length === 1 ? 'run' : 'runs'}</b>
    </header>
    {error ? <p className="agent-inline-error payment-audit-error">{error}</p> : null}
    {loading ? <p className="agent-empty-copy">Loading payment audit…</p> : null}
    {!loading && runs.length === 0 ? <p className="agent-empty-copy">No purchase or payment runs yet.</p> : null}
    <div className="payment-run-list">
      {runs.map((run) => <article className="run-card" key={run.run_id}>
        <button type="button" onClick={() => toggle(run.run_id)} aria-expanded={open === run.run_id}>
          <span><strong>{run.product_title ?? 'Agent purchase'}</strong><small>{when(run.created_at)}</small></span>
          <span><b>{money(run.amount_paise)}</b><em className={`run-state state-${run.state.toLowerCase()}`}>{run.state}</em></span>
          <i aria-hidden="true">{open === run.run_id ? '−' : '+'}</i>
        </button>
        {open === run.run_id ? <div className="run-detail">
          <dl className="payment-run-facts">
            <div><dt>Run state</dt><dd>{run.state}</dd></div>
            <div><dt>Payment state</dt><dd>{valueOrFallback(run.payment_state, 'Not started')}</dd></div>
            <div><dt>Provider order ID</dt><dd>{valueOrFallback(run.provider_order_id, 'Not created')}</dd></div>
            <div><dt>Fulfilment status</dt><dd>{valueOrFallback(run.fulfillment_status, 'Not created')}</dd></div>
            <div><dt>Terminal reason</dt><dd>{valueOrFallback(run.terminal_reason)}</dd></div>
            <div><dt>Run ID</dt><dd>{run.run_id}</dd></div>
          </dl>
          {run.fulfillment_order_number ? <p>Order <strong>{run.fulfillment_order_number}</strong></p> : null}
          {run.shipping_address ? <p>{run.shipping_address.full_name} · {run.shipping_address.city}, {run.shipping_address.state} {run.shipping_address.postal_code}</p> : null}
          <div className="audit-timeline" aria-label={`Audit entries for ${run.product_title ?? 'purchase run'}`}>
            {(audit[run.run_id] ?? []).map((entry) => <div key={entry.audit_id} className="audit-entry">
              <i /><div><span>{entry.sequence_number}. {entry.action}</span><strong>{entry.outcome}</strong><p>{entry.explanation}</p><small>{entry.actor} · {entry.signed ? 'signature verified' : 'hash-chain entry'} · {when(entry.created_at)}</small></div>
            </div>)}
            {loadingAudit === run.run_id ? <p>Loading audit timeline…</p> : null}
            {audit[run.run_id]?.length === 0 ? <p>No audit entries were recorded for this run.</p> : null}
          </div>
        </div> : null}
      </article>)}
    </div>
  </section>
}

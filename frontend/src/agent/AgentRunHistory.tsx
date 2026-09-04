import { useEffect, useState } from 'react'
import { ApiError, fetchAgentRuns, fetchRunAudit } from '../api'
import type { AgentRunHistoryItem, AuditHistoryItem } from '../types'

const money = (paise: number | null) => paise === null ? '—' : new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(paise / 100)
const when = (value: string) => new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))

export default function AgentRunHistory({ refreshKey = 0 }: { refreshKey?: number }) {
  const [runs, setRuns] = useState<AgentRunHistoryItem[]>([])
  const [open, setOpen] = useState<string | null>(null)
  const [audit, setAudit] = useState<Record<string, AuditHistoryItem[]>>({})
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    fetchAgentRuns(controller.signal).then((data) => setRuns(data.items)).catch((reason: unknown) => {
      if (reason instanceof DOMException && reason.name === 'AbortError') return
      if (reason instanceof ApiError && reason.status === 404) { setRuns([]); return }
      setError(reason instanceof Error ? reason.message : 'Run history is unavailable.')
    })
    return () => controller.abort()
  }, [refreshKey])

  async function toggle(runId: string) {
    if (open === runId) { setOpen(null); return }
    setOpen(runId)
    if (audit[runId]) return
    try { const result = await fetchRunAudit(runId); setAudit((current) => ({ ...current, [runId]: result.items })) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Audit history is unavailable.') }
  }

  return <section className="agent-history" aria-label="Agent run history">
    <div className="agent-panel-title"><span>Governed runs</span><b>{runs.length}</b></div>
    {error ? <p className="agent-inline-error">{error}</p> : null}
    {runs.length === 0 ? <p className="agent-empty-copy">No proposals or payment runs yet.</p> : null}
    {runs.map((run) => <article className="run-card" key={run.run_id}>
      <button type="button" onClick={() => toggle(run.run_id)} aria-expanded={open === run.run_id}>
        <span><strong>{run.product_title ?? 'Agent proposal'}</strong><small>{when(run.created_at)}</small></span>
        <span><b>{money(run.amount_paise)}</b><em className={`run-state state-${run.state.toLowerCase()}`}>{run.fulfillment_status ?? run.state}</em></span>
      </button>
      {open === run.run_id ? <div className="run-detail">
        {run.fulfillment_order_number ? <p>Order <strong>{run.fulfillment_order_number}</strong></p> : null}
        {run.terminal_reason ? <p className="agent-inline-error">{run.terminal_reason}</p> : null}
        {run.shipping_address ? <p>{run.shipping_address.full_name} · {run.shipping_address.city}, {run.shipping_address.state} {run.shipping_address.postal_code}</p> : null}
        <div className="audit-timeline">
          {(audit[run.run_id] ?? []).map((entry) => <div key={entry.audit_id} className="audit-entry">
            <i /><div><span>{entry.sequence_number}. {entry.action}</span><strong>{entry.outcome}</strong><p>{entry.explanation}</p><small>{entry.actor} · {entry.signed ? 'signature verified' : 'hash-chain entry'} · {when(entry.created_at)}</small></div>
          </div>)}
          {!audit[run.run_id] ? <p>Loading audit timeline…</p> : null}
        </div>
      </div> : null}
    </article>)}
  </section>
}

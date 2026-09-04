import { useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, closeAgentConversation, createAgentConversation, fetchAgentConversation, fetchAgentConversations, sendAgentChat } from '../api'
import type { AccountProfile, AgentChatRequest, AgentChatResponse, AgentConversationDetail, AgentConversationSummary, PurchaseProposal } from '../types'
import AgentCheckout from './AgentCheckout'
import AgentRunHistory from './AgentRunHistory'
import './agent-workspace.css'

const id = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`
const money = (paise: number) => new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(paise / 100)
type ViewTurn = { id: string; role: 'user' | 'agent'; text: string; response?: AgentChatResponse }

export default function AgentWorkspace({ profile, sessionChecked, onSignIn }: { profile: AccountProfile | null; sessionChecked: boolean; onSignIn: () => void }) {
  const [conversations, setConversations] = useState<AgentConversationSummary[]>([])
  const [current, setCurrent] = useState<AgentConversationDetail | null>(null)
  const [turns, setTurns] = useState<ViewTurn[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [proposal, setProposal] = useState<PurchaseProposal | null>(null)
  const [historyKey, setHistoryKey] = useState(0)
  const endRef = useRef<HTMLDivElement>(null)

  const latest = useMemo(() => [...turns].reverse().find((turn) => turn.response)?.response ?? null, [turns])
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [turns, busy])
  useEffect(() => {
    if (!profile) { setConversations([]); setCurrent(null); return }
    const controller = new AbortController()
    fetchAgentConversations(controller.signal).then((result) => { setConversations(result.items); if (result.items[0]) void openConversation(result.items[0].conversation_id) }).catch((reason: unknown) => { if (!(reason instanceof DOMException && reason.name === 'AbortError')) setError(reason instanceof Error ? reason.message : 'Conversations are unavailable.') })
    return () => controller.abort()
  }, [profile?.id])

  async function openConversation(conversationId: string) {
    setBusy(true); setError(null)
    try {
      const detail = await fetchAgentConversation(conversationId); setCurrent(detail)
      const restored: ViewTurn[] = []
      detail.turns.forEach((turn) => { restored.push({ id: `${turn.turn_id}-u`, role: 'user', text: turn.user_message }); restored.push({ id: turn.turn_id, role: 'agent', text: turn.assistant_reply, response: turn.response }) })
      setTurns(restored); setProposal([...detail.turns].reverse().find((turn) => turn.response.purchase_proposal)?.response.purchase_proposal ?? null)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Conversation could not be opened.') } finally { setBusy(false) }
  }
  async function newConversation() {
    if (!profile) { onSignIn(); return }
    setBusy(true); setError(null)
    try { const created = await createAgentConversation(); setConversations((items) => [created, ...items]); setCurrent({ ...created, turns: [] }); setTurns([]); setProposal(null) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Conversation could not be created.') } finally { setBusy(false) }
  }
  async function closeCurrent() {
    if (!current) return
    await closeAgentConversation(current.conversation_id); setConversations((items) => items.filter((item) => item.conversation_id !== current.conversation_id)); setCurrent(null); setTurns([]); setProposal(null)
  }

  async function submit(message: string, extra: Partial<AgentChatRequest> = {}) {
    const text = message.trim(); if (!text || busy) return
    const clientTurnId = id(); setTurns((items) => [...items, { id: `${clientTurnId}-u`, role: 'user', text }]); setDraft(''); setBusy(true); setError(null)
    const request: AgentChatRequest = { message: text, limit: 4, client_turn_id: clientTurnId, ...extra }
    if (profile && current) { request.conversation_id = current.conversation_id; request.expected_conversation_version = current.version }
    try {
      const response = await sendAgentChat(request); setTurns((items) => [...items, { id: response.turn_id ?? id(), role: 'agent', text: response.reply, response }])
      if (response.purchase_proposal) setProposal(response.purchase_proposal)
      if (profile && response.conversation_id) {
        const detail = await fetchAgentConversation(response.conversation_id); setCurrent(detail)
        const list = await fetchAgentConversations(); setConversations(list.items)
      }
    } catch (reason) {
      if (reason instanceof ApiError && reason.code === 'CONVERSATION_CHANGED' && current) { await openConversation(current.conversation_id); setError('Conversation changed in another tab. It was reloaded; send again when ready.') }
      else setError(reason instanceof Error ? reason.message : 'The Agent could not complete this turn.')
    } finally { setBusy(false) }
  }

  function recommendationCards(response: AgentChatResponse) {
    return <>
      {response.clarification ? <div className="clarification-options">{response.clarification.options.map((option) => <button type="button" key={option.product_id} onClick={() => submit(option.label, { selected_product_id: option.product_id })}>{option.label}</button>)}</div> : null}
      <div className="agent-recommendations">{response.recommendations.map((item) => <article className="agent-recommendation" key={item.product.id}><div>{response.exact_match && response.focus_product_id === item.product.id ? <span className="exact-badge">Exact catalogue match</span> : null}<h3>{item.product.title}</h3><p>{item.reasons.slice(0, 2).join(' · ')}</p><strong>{money(item.product.offer_price_paise)}</strong></div><small>{item.score}/100</small></article>)}</div>
      {response.cross_sell ? <article className="agent-recommendation"><div><span className="exact-badge">Optional add-on</span><h3>{response.cross_sell.product.title}</h3><strong>{money(response.cross_sell.product.offer_price_paise)}</strong><p>Ask the Agent to buy this separately if you want it.</p></div></article> : null}
      {response.cross_sell_consent_required ? <div className="cross-sell-consent"><span>Show optional add-ons?</span><button type="button" onClick={() => submit('Show optional add-ons', { cross_sell_consent: true })}>Yes, show separately</button><button type="button" onClick={() => submit('No add-ons', { cross_sell_consent: false })}>No thanks</button></div> : null}
      {response.outcome === 'NO_MATCH' || response.outcome === 'BLOCKED' ? <div className="agent-recovery">{(response.remaining_replans ?? 0) > 0 ? <><button type="button" onClick={() => submit('Show me a cheaper option')}>Try cheaper</button><button type="button" onClick={() => submit('Show me another option')}>Try another</button></> : <button type="button" onClick={newConversation}>Start a fresh session</button>}</div> : null}
    </>
  }

  return <main className="agent-workspace">
    <header className="agent-workspace-head"><div><span className="section-label">GOVERNED AI BUYER</span><h1>Shopy Agent</h1><p>Exact catalogue resolution, policy-bounded recommendations, explicit address and Razorpay confirmation, with every decision recorded.</p></div><button className="agent-new-button" type="button" onClick={newConversation}>+ New session</button></header>
    <div className="agent-layout">
      <aside className="agent-sidebar"><div className="agent-panel-title"><span>Sessions</span>{current ? <button type="button" className="agent-text-button" onClick={closeCurrent}>Close</button> : null}</div>{!profile && sessionChecked ? <div className="agent-empty-copy"><p>Guest search works, but sign in to save sessions and buy.</p><button type="button" onClick={onSignIn}>Sign in</button></div> : <div className="conversation-list">{conversations.map((conversation) => <button type="button" className={current?.conversation_id === conversation.conversation_id ? 'active' : ''} key={conversation.conversation_id} onClick={() => openConversation(conversation.conversation_id)}><strong>{conversation.title}</strong><small>{conversation.last_message_preview ?? 'New conversation'}</small></button>)}</div>}</aside>
      <section className="agent-thread"><div className="agent-messages-full" aria-live="polite">{turns.length === 0 ? <div className="agent-turn"><div className="agent-turn-bubble">Tell me an exact brand/model, category, feature, or budget. I will clarify ambiguity instead of guessing.</div><div className="agent-recovery"><button type="button" onClick={() => setDraft('Find iPhone 16')}>Find iPhone 16</button><button type="button" onClick={() => setDraft('Wireless headphones under ₹20,000')}>Headphones under ₹20k</button></div></div> : null}{turns.map((turn) => <div className={`agent-turn ${turn.role}`} key={turn.id}><div className="agent-turn-bubble">{turn.text}</div>{turn.response ? recommendationCards(turn.response) : null}</div>)}{busy ? <div className="agent-typing-full">Checking live catalogue and policy…</div> : null}<div ref={endRef}/></div><form className="agent-composer" onSubmit={(event) => { event.preventDefault(); void submit(draft) }}><input value={draft} onChange={(event) => setDraft(event.target.value)} maxLength={1000} placeholder="Ask for a product or say cheaper / another…"/><button type="submit" disabled={!draft.trim() || busy}>→</button></form>{error ? <p className="agent-inline-error" style={{ padding: '0 18px 16px' }}>{error}</p> : null}</section>
      <aside className="agent-inspector">{proposal ? <AgentCheckout proposal={proposal} signedIn={profile !== null} onSignIn={onSignIn} onRunChange={() => setHistoryKey((value) => value + 1)}/> : <section className="agent-checkout-gate"><strong>Recommendation mode</strong><p>Ask for recommendations to compare only. Say “buy me…” or “order…” when you want address confirmation and Razorpay checkout.</p></section>}{profile ? <AgentRunHistory refreshKey={historyKey}/> : null}{latest?.notice ? <p className="agent-empty-copy">{latest.notice}</p> : null}</aside>
    </div>
  </main>
}

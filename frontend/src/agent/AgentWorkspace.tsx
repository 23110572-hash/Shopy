import { useEffect, useRef, useState } from 'react'
import { ApiError, closeAgentConversation, createAgentConversation, fetchAgentConversation, fetchAgentConversations, sendAgentChat } from '../api'
import type { AccountProfile, AgentChatRequest, AgentChatResponse, AgentConversationDetail, AgentConversationSummary, PurchaseProposal } from '../types'
import AgentCheckout from './AgentCheckout'
import './agent-workspace.css'

const id = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`
const money = (paise: number) => new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(paise / 100)
type ViewTurn = { id: string; role: 'user' | 'agent'; text: string; response?: AgentChatResponse }

type BrowserSpeechResult = { readonly isFinal: boolean; readonly 0?: { readonly transcript: string } }
type BrowserSpeechResultEvent = { readonly results: ArrayLike<BrowserSpeechResult> }
type BrowserSpeechErrorEvent = { readonly error: string }
interface BrowserSpeechRecognition {
  lang: string
  continuous: boolean
  interimResults: boolean
  onresult: ((event: BrowserSpeechResultEvent) => void) | null
  onerror: ((event: BrowserSpeechErrorEvent) => void) | null
  onend: (() => void) | null
  start: () => void
  stop: () => void
  abort: () => void
}
type BrowserSpeechRecognitionConstructor = new () => BrowserSpeechRecognition
type SpeechCapableWindow = Window & typeof globalThis & {
  SpeechRecognition?: BrowserSpeechRecognitionConstructor
  webkitSpeechRecognition?: BrowserSpeechRecognitionConstructor
}

type MicrophonePermissionState = PermissionState | 'unknown'

function speechErrorMessage(code: string, permission: MicrophonePermissionState): string {
  if (code === 'service-not-allowed') return 'Your browser speech-recognition service is unavailable. Microphone permission is already separate; try Chrome or Edge, or type your message.'
  if (code === 'not-allowed' && permission === 'denied') return 'Microphone is blocked for this site. Click the site-controls icon beside the address bar, set Microphone to Allow, then try again.'
  if (code === 'not-allowed' && permission === 'granted') return 'Microphone access is allowed, but the browser refused speech recognition. Click the mic again or check browser speech-service settings.'
  if (code === 'not-allowed') return 'The browser did not allow speech recognition. Confirm the microphone prompt, then click the mic again.'
  if (code === 'no-speech') return 'No speech was detected. Please try again.'
  if (code === 'audio-capture') return 'No working microphone was found.'
  if (code === 'network') return 'Speech recognition is temporarily unavailable.'
  return 'Speech recognition could not understand that. Please try again.'
}

export default function AgentWorkspace({ profile, sessionChecked, onSignIn, onClose }: { profile: AccountProfile | null; sessionChecked: boolean; onSignIn: () => void; onClose: () => void }) {
  const [conversations, setConversations] = useState<AgentConversationSummary[]>([])
  const [current, setCurrent] = useState<AgentConversationDetail | null>(null)
  const [turns, setTurns] = useState<ViewTurn[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [proposal, setProposal] = useState<PurchaseProposal | null>(null)
  const [listening, setListening] = useState(false)
  const [requestingMic, setRequestingMic] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)
  const speechRef = useRef<BrowserSpeechRecognition | null>(null)
  const microphoneReadyRef = useRef(false)
  const microphonePermissionRef = useRef<MicrophonePermissionState>('unknown')

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [turns, busy])
  useEffect(() => {
    if (!profile) { setConversations([]); setCurrent(null); return }
    const controller = new AbortController()
    fetchAgentConversations(controller.signal).then((result) => { setConversations(result.items); if (result.items[0]) void openConversation(result.items[0].conversation_id) }).catch((reason: unknown) => { if (!(reason instanceof DOMException && reason.name === 'AbortError')) setError(reason instanceof Error ? reason.message : 'Conversations are unavailable.') })
    return () => controller.abort()
  }, [profile?.id])
  useEffect(() => {
    if (!navigator.permissions?.query) return
    let active = true
    let permissionStatus: PermissionStatus | null = null
    void navigator.permissions.query({ name: 'microphone' as PermissionName }).then((status) => {
      if (!active) return
      permissionStatus = status
      const syncPermission = () => {
        microphonePermissionRef.current = status.state
        microphoneReadyRef.current = status.state === 'granted'
      }
      syncPermission()
      status.onchange = syncPermission
    }).catch(() => { microphonePermissionRef.current = 'unknown' })
    return () => { active = false; if (permissionStatus) permissionStatus.onchange = null }
  }, [])
  useEffect(() => () => {
    const recognition = speechRef.current
    if (!recognition) return
    recognition.onresult = null
    recognition.onerror = null
    recognition.onend = null
    recognition.abort()
    speechRef.current = null
  }, [])

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

  async function requestMicrophoneAccess() {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      setError('Microphone access requires HTTPS and a browser with media-device support.')
      return
    }
    if (microphonePermissionRef.current === 'denied') {
      setError('Microphone is blocked for this site. Click the site-controls icon beside the address bar, set Microphone to Allow, then try again.')
      return
    }
    setRequestingMic(true)
    setError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      stream.getTracks().forEach((track) => track.stop())
      microphoneReadyRef.current = true
      microphonePermissionRef.current = 'granted'
      setError('Microphone is allowed. Click the mic again to speak.')
    } catch (reason) {
      if (reason instanceof DOMException && (reason.name === 'NotAllowedError' || reason.name === 'SecurityError')) {
        microphonePermissionRef.current = 'denied'
        setError('Microphone is blocked for this site. Click the site-controls icon beside the address bar, set Microphone to Allow, then try again.')
      } else if (reason instanceof DOMException && reason.name === 'NotFoundError') {
        setError('No working microphone was found.')
      } else {
        setError('The browser could not open your microphone. Check the selected input device and try again.')
      }
    } finally {
      setRequestingMic(false)
    }
  }

  function startSpeechRecognition(SpeechRecognition: BrowserSpeechRecognitionConstructor) {
    const recognition = new SpeechRecognition()
    let submitted = false
    recognition.lang = 'en-IN'
    recognition.continuous = false
    recognition.interimResults = false
    recognition.onresult = (event) => {
      const transcript = Array.from(event.results)
        .filter((result) => result.isFinal)
        .map((result) => result[0]?.transcript ?? '')
        .join(' ')
        .trim()
      if (!transcript || submitted) return
      submitted = true
      setDraft(transcript)
      void submit(transcript)
    }
    recognition.onerror = (event) => {
      if (event.error !== 'aborted') setError(speechErrorMessage(event.error, microphonePermissionRef.current))
      setListening(false)
      if (speechRef.current === recognition) speechRef.current = null
    }
    recognition.onend = () => {
      setListening(false)
      if (speechRef.current === recognition) speechRef.current = null
    }
    speechRef.current = recognition
    setError(null)
    try {
      recognition.start()
      setListening(true)
    } catch {
      speechRef.current = null
      setListening(false)
      setError('The microphone could not be started. Click the mic again or try Chrome or Edge.')
    }
  }

  function toggleSpeechRecognition() {
    if (speechRef.current) {
      speechRef.current.stop()
      return
    }
    const speechWindow = window as SpeechCapableWindow
    const SpeechRecognition = speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition
    if (!SpeechRecognition) {
      setError('Speech recognition is not supported in this browser. Try the latest Chrome or Edge.')
      return
    }
    if (microphonePermissionRef.current === 'granted' || microphoneReadyRef.current) {
      startSpeechRecognition(SpeechRecognition)
      return
    }
    if (microphonePermissionRef.current === 'denied') {
      setError('Microphone is blocked for this site. Click the site-controls icon beside the address bar, set Microphone to Allow, then try again.')
      return
    }
    void requestMicrophoneAccess()
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

  return <div className="agent-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
    <main className="agent-workspace agent-modal" role="dialog" aria-modal="true" aria-label="Shopy Agent">
      <button className="agent-modal-close" type="button" onClick={onClose} aria-label="Close Shopy Agent">×</button>
      <header className="agent-workspace-head"><div><span className="section-label">GOVERNED AI BUYER</span><h1>Shopy Agent</h1><p>Exact catalogue resolution, policy-bounded recommendations, explicit address and Razorpay confirmation, with every decision recorded.</p></div><button className="agent-new-button" type="button" onClick={newConversation}>+ New session</button></header>
      <div className="agent-layout">
        <aside className="agent-sidebar"><div className="agent-panel-title"><span>Sessions</span>{current ? <button type="button" className="agent-text-button" onClick={closeCurrent}>Close</button> : null}</div>{!profile && sessionChecked ? <div className="agent-empty-copy"><p>Guest search works, but sign in to save sessions and buy.</p><button type="button" onClick={onSignIn}>Sign in</button></div> : <div className="conversation-list">{conversations.map((conversation) => <button type="button" className={current?.conversation_id === conversation.conversation_id ? 'active' : ''} key={conversation.conversation_id} onClick={() => openConversation(conversation.conversation_id)}><strong>{conversation.title}</strong><small>{conversation.last_message_preview ?? 'New conversation'}</small></button>)}</div>}</aside>
        <section className="agent-thread"><div className="agent-messages-full" aria-live="polite">{turns.length === 0 ? <div className="agent-turn"><div className="agent-turn-bubble">Hello, I am Shopy. How can I help you?</div><div className="agent-recovery"><button type="button" onClick={() => setDraft('Find iPhone 16')}>Find iPhone 16</button><button type="button" onClick={() => setDraft('Wireless headphones under ₹20,000')}>Headphones under ₹20k</button></div></div> : null}{turns.map((turn) => <div className={`agent-turn ${turn.role}`} key={turn.id}><div className="agent-turn-bubble">{turn.text}</div>{turn.response ? recommendationCards(turn.response) : null}</div>)}{proposal ? <div className="agent-inline-checkout"><AgentCheckout proposal={proposal} signedIn={profile !== null} onSignIn={onSignIn} onRunChange={() => undefined}/></div> : null}{busy ? <div className="agent-typing-full">Checking live catalogue and policy…</div> : null}<div ref={endRef}/></div><form className="agent-composer" onSubmit={(event) => { event.preventDefault(); void submit(draft) }}><input value={draft} onChange={(event) => setDraft(event.target.value)} maxLength={1000} placeholder="Ask for a product or say cheaper / another…"/><button className={`agent-mic${listening ? ' listening' : ''}${requestingMic ? ' requesting' : ''}`} type="button" onClick={toggleSpeechRecognition} disabled={busy || requestingMic} aria-label={requestingMic ? 'Requesting microphone access' : listening ? 'Stop listening' : 'Speak your message'} aria-pressed={listening}><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3M9 21h6"/></svg></button><button className="agent-send" type="submit" disabled={!draft.trim() || busy} aria-label="Send message">→</button></form>{error ? <p className="agent-inline-error" style={{ padding: '0 18px 16px' }}>{error}</p> : null}</section>
      </div>
    </main>
  </div>
}

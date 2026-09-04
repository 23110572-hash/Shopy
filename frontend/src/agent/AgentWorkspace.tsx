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
  start: (audioTrack?: MediaStreamTrack) => void
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
  if (code === 'service-not-allowed') return 'Your browser speech-recognition service is unavailable. Microphone permission is separate; try Chrome or Edge, or type your message.'
  if (code === 'not-allowed' && permission === 'granted') return 'Microphone permission is allowed, but the browser speech-recognition service refused to start. Check Chrome or Edge speech settings, then try again.'
  if (code === 'not-allowed' && permission === 'denied') return 'Microphone access is denied for this page. Check the site setting, Windows microphone privacy, and browser policy, then try again.'
  if (code === 'not-allowed') return 'The browser did not allow speech recognition. Check microphone and browser speech settings, then try again.'
  if (code === 'no-speech') return 'No speech was detected. Please try again.'
  if (code === 'audio-capture') return 'The microphone could not be opened. Check the selected input device and make sure another application is not using it.'
  if (code === 'network') return 'The browser speech-recognition service is temporarily unavailable.'
  if (code === 'language-not-supported') return 'The browser speech-recognition service does not support the selected language.'
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
  const inputRef = useRef<HTMLInputElement>(null)
  const speechRef = useRef<BrowserSpeechRecognition | null>(null)
  const speechAttemptRef = useRef(0)
  const microphoneStreamRef = useRef<MediaStream | null>(null)
  const microphonePermissionRef = useRef<MicrophonePermissionState>('unknown')

  function focusComposer() {
    window.requestAnimationFrame(() => inputRef.current?.focus())
  }

  function releaseMicrophoneStream() {
    const stream = microphoneStreamRef.current
    if (!stream) return
    stream.getTracks().forEach((track) => track.stop())
    microphoneStreamRef.current = null
  }

  function stopSpeechRecognition() {
    speechAttemptRef.current += 1
    const recognition = speechRef.current
    if (recognition) {
      recognition.onresult = null
      recognition.onerror = null
      recognition.onend = null
      recognition.abort()
      speechRef.current = null
    }
    releaseMicrophoneStream()
    setListening(false)
    setRequestingMic(false)
  }

  function resetVisibleSession() {
    stopSpeechRecognition()
    setTurns([])
    setDraft('')
    setProposal(null)
    setError(null)
    focusComposer()
  }

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [turns, busy])
  useEffect(() => { if (!busy) focusComposer() }, [busy, current?.conversation_id])
  useEffect(() => {
    if (!profile) {
      setConversations([])
      setCurrent(null)
      resetVisibleSession()
      return
    }
    const controller = new AbortController()
    fetchAgentConversations(controller.signal).then((result) => {
      setConversations(result.items)
      if (result.items[0]) void openConversation(result.items[0].conversation_id)
      else {
        setCurrent(null)
        resetVisibleSession()
      }
    }).catch((reason: unknown) => {
      if (!(reason instanceof DOMException && reason.name === 'AbortError')) setError(reason instanceof Error ? reason.message : 'Conversations are unavailable.')
    })
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
      }
      syncPermission()
      status.onchange = syncPermission
    }).catch(() => { microphonePermissionRef.current = 'unknown' })
    return () => { active = false; if (permissionStatus) permissionStatus.onchange = null }
  }, [])
  useEffect(() => () => { stopSpeechRecognition() }, [])

  async function openConversation(conversationId: string) {
    stopSpeechRecognition()
    setBusy(true); setError(null); setDraft('')
    try {
      const detail = await fetchAgentConversation(conversationId); setCurrent(detail)
      const restored: ViewTurn[] = []
      detail.turns.forEach((turn) => { restored.push({ id: `${turn.turn_id}-u`, role: 'user', text: turn.user_message }); restored.push({ id: turn.turn_id, role: 'agent', text: turn.assistant_reply, response: turn.response }) })
      setTurns(restored); setProposal([...detail.turns].reverse().find((turn) => turn.response.purchase_proposal)?.response.purchase_proposal ?? null)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Conversation could not be opened.') } finally { setBusy(false) }
  }

  async function newConversation() {
    if (busy) return
    if (!profile) {
      setCurrent(null)
      resetVisibleSession()
      return
    }
    stopSpeechRecognition()
    setBusy(true); setError(null)
    try {
      const created = await createAgentConversation()
      setConversations((items) => [created, ...items.filter((item) => item.conversation_id !== created.conversation_id)])
      setCurrent({ ...created, turns: [] })
      setTurns([]); setDraft(''); setProposal(null)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Conversation could not be created.') } finally { setBusy(false) }
  }

  async function closeCurrent() {
    if (!current || busy) return
    setBusy(true); setError(null)
    try {
      await closeAgentConversation(current.conversation_id)
      setConversations((items) => items.filter((item) => item.conversation_id !== current.conversation_id))
      setCurrent(null)
      resetVisibleSession()
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Conversation could not be closed.') } finally { setBusy(false) }
  }

  async function submit(message: string, extra: Partial<AgentChatRequest> = {}) {
    const text = message.trim(); if (!text || busy) return
    const clientTurnId = id(); setTurns((items) => [...items, { id: `${clientTurnId}-u`, role: 'user', text }]); setDraft(''); setProposal(null); setBusy(true); setError(null)
    const request: AgentChatRequest = { message: text, limit: 4, client_turn_id: clientTurnId, ...extra }
    if (profile && current) { request.conversation_id = current.conversation_id; request.expected_conversation_version = current.version }
    try {
      const response = await sendAgentChat(request); setTurns((items) => [...items, { id: response.turn_id ?? id(), role: 'agent', text: response.reply, response }])
      setProposal(response.purchase_proposal ?? null)
      if (profile && response.conversation_id) {
        const detail = await fetchAgentConversation(response.conversation_id); setCurrent(detail)
        const list = await fetchAgentConversations(); setConversations(list.items)
      }
    } catch (reason) {
      if (reason instanceof ApiError && reason.code === 'CONVERSATION_CHANGED' && current) { await openConversation(current.conversation_id); setError('Conversation changed in another tab. It was reloaded; send again when ready.') }
      else setError(reason instanceof Error ? reason.message : 'The Agent could not complete this turn.')
    } finally { setBusy(false) }
  }

  async function readMicrophonePermission(): Promise<MicrophonePermissionState> {
    if (!navigator.permissions?.query) return 'unknown'
    try {
      const status = await navigator.permissions.query({ name: 'microphone' as PermissionName })
      microphonePermissionRef.current = status.state
      return status.state
    } catch {
      return 'unknown'
    }
  }

  function startSpeechRecognition(
    SpeechRecognition: BrowserSpeechRecognitionConstructor,
    attempt: number,
    audioTrack: MediaStreamTrack,
  ) {
    const recognition = new SpeechRecognition()
    let submitted = false
    recognition.lang = 'en-IN'
    recognition.continuous = false
    recognition.interimResults = false
    recognition.onresult = (event) => {
      if (speechAttemptRef.current !== attempt || speechRef.current !== recognition) return
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
      if (speechAttemptRef.current !== attempt || speechRef.current !== recognition) return
      setListening(false)
      speechRef.current = null
      releaseMicrophoneStream()
      if (event.error === 'aborted') return
      if (event.error === 'not-allowed') {
        void readMicrophonePermission().then((permission) => {
          if (speechAttemptRef.current === attempt) setError(speechErrorMessage(event.error, permission))
        })
        return
      }
      setError(speechErrorMessage(event.error, microphonePermissionRef.current))
    }
    recognition.onend = () => {
      if (speechAttemptRef.current !== attempt || speechRef.current !== recognition) return
      setListening(false)
      speechRef.current = null
      releaseMicrophoneStream()
    }
    speechRef.current = recognition
    setError(null)
    try {
      try {
        recognition.start(audioTrack)
      } catch (reason) {
        if (!(reason instanceof TypeError)) throw reason
        recognition.start()
      }
      setListening(true)
    } catch {
      if (speechAttemptRef.current === attempt && speechRef.current === recognition) {
        recognition.onresult = null
        recognition.onerror = null
        recognition.onend = null
        speechRef.current = null
        releaseMicrophoneStream()
        setListening(false)
        setError('The browser could not start speech recognition from the microphone. Try again in the latest Chrome, or type your message.')
      }
    }
  }

  async function requestMicrophoneAndStart(SpeechRecognition: BrowserSpeechRecognitionConstructor) {
    const attempt = speechAttemptRef.current + 1
    speechAttemptRef.current = attempt
    setRequestingMic(true)
    setError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      if (speechAttemptRef.current !== attempt) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      const audioTrack = stream.getAudioTracks()[0]
      if (!audioTrack) {
        stream.getTracks().forEach((track) => track.stop())
        setError('Chrome opened the microphone but did not provide an audio track. Check the selected input device.')
        return
      }
      releaseMicrophoneStream()
      microphoneStreamRef.current = stream
      microphonePermissionRef.current = 'granted'
      setRequestingMic(false)
      startSpeechRecognition(SpeechRecognition, attempt, audioTrack)
    } catch (reason) {
      if (speechAttemptRef.current !== attempt) return
      if (reason instanceof DOMException && (reason.name === 'NotAllowedError' || reason.name === 'SecurityError')) {
        const permission = await readMicrophonePermission()
        if (speechAttemptRef.current !== attempt) return
        if (permission === 'granted') {
          setError('Chrome reports that this site is allowed, but Windows or browser policy refused microphone capture. Check Windows Settings → Privacy & security → Microphone and allow desktop apps.')
        } else if (permission === 'denied') {
          setError(`Chrome denied microphone capture for ${window.location.origin}. Remove this site from Chrome's blocked microphone list, reload the page, and allow the new prompt.`)
        } else {
          setError('Chrome did not allow microphone capture. Reload the page and allow the microphone prompt.')
        }
      } else if (reason instanceof DOMException && reason.name === 'NotFoundError') {
        setError('Chrome could not find a microphone. Select a working input device in Chrome settings.')
      } else if (reason instanceof DOMException && reason.name === 'NotReadableError') {
        setError('Chrome found the microphone but could not open it. Close other apps using the microphone and try again.')
      } else {
        setError('Chrome could not open the microphone. Check the selected input device and try again.')
      }
    } finally {
      if (speechAttemptRef.current === attempt) setRequestingMic(false)
    }
  }

  function toggleSpeechRecognition() {
    if (speechRef.current) {
      speechRef.current.stop()
      return
    }
    if (requestingMic) return
    if (!window.isSecureContext) {
      setError('Microphone access requires HTTPS or localhost.')
      return
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setError('This browser cannot request microphone audio. Try the latest Chrome or Edge.')
      return
    }
    const speechWindow = window as SpeechCapableWindow
    const SpeechRecognition = speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition
    if (!SpeechRecognition) {
      setError('Speech recognition is not supported in this browser. Try the latest Chrome or Edge.')
      return
    }
    void requestMicrophoneAndStart(SpeechRecognition)
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
      <header className="agent-workspace-head"><div><span className="section-label">GOVERNED AI BUYER</span><h1>Shopy Agent</h1></div><button className="agent-new-button" type="button" onClick={newConversation} disabled={busy}>+ New session</button></header>
      <div className="agent-layout">
        <aside className="agent-sidebar"><div className="agent-panel-title"><span>Sessions</span>{current ? <button type="button" className="agent-text-button" onClick={closeCurrent} disabled={busy}>Close</button> : null}</div>{!profile && sessionChecked ? <div className="agent-empty-copy"><p>Guest search works, but sign in to save sessions and buy.</p><button type="button" onClick={onSignIn}>Sign in</button></div> : <div className="conversation-list">{conversations.map((conversation) => <button type="button" className={current?.conversation_id === conversation.conversation_id ? 'active' : ''} key={conversation.conversation_id} onClick={() => openConversation(conversation.conversation_id)} disabled={busy}><strong>{conversation.title}</strong><small>{conversation.last_message_preview ?? 'New conversation'}</small></button>)}</div>}</aside>
        <section className="agent-thread"><div className="agent-messages-full" aria-live="polite">{turns.length === 0 ? <div className="agent-turn"><div className="agent-turn-bubble">Hello, I am Shopy. How can I help you?</div></div> : null}{turns.map((turn) => <div className={`agent-turn ${turn.role}`} key={turn.id}><div className="agent-turn-bubble">{turn.text}</div>{turn.response ? recommendationCards(turn.response) : null}</div>)}{proposal ? <div className="agent-inline-checkout"><AgentCheckout key={proposal.proposal_id} proposal={proposal} signedIn={profile !== null} onSignIn={onSignIn} onRunChange={() => undefined}/></div> : null}{busy ? <div className="agent-typing-full">Shopy is working on your request…</div> : null}<div ref={endRef}/></div><form className="agent-composer" onSubmit={(event) => { event.preventDefault(); void submit(draft) }}><input ref={inputRef} value={draft} onChange={(event) => setDraft(event.target.value)} maxLength={1000} placeholder="Ask for a product or say cheaper / another…"/><button className={`agent-mic${listening ? ' listening' : ''}${requestingMic ? ' requesting' : ''}`} type="button" onClick={toggleSpeechRecognition} disabled={busy || requestingMic} aria-label={requestingMic ? 'Requesting microphone access' : listening ? 'Stop listening' : 'Speak your message'} aria-pressed={listening}><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3M9 21h6"/></svg></button><button className="agent-send" type="submit" disabled={!draft.trim() || busy} aria-label="Send message">→</button></form>{error ? <p className="agent-inline-error" style={{ padding: '0 18px 16px' }}>{error}</p> : null}</section>
      </div>
    </main>
  </div>
}

import { useEffect, useRef, useState } from 'react'
import {
  ApiError,
  closeAgentConversation,
  createAgentConversation,
  fetchAgentConversation,
  fetchAgentConversations,
  sendAgentChat,
} from '../api'
import type {
  AccountProfile,
  AgentChatRequest,
  AgentChatResponse,
  AgentConversationDetail,
  AgentConversationSummary,
  PurchaseProposal,
} from '../types'
import AgentCheckout from './AgentCheckout'
import './agent-workspace.css'

const id = () => globalThis.crypto?.randomUUID?.()
  ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`
const money = (paise: number) => new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
}).format(paise / 100)
const MAX_CHAT_MESSAGE_LENGTH = 1_000
const detailCacheKey = (conversationId: string, version: number) => `${conversationId}:${version}`
const joinSpeech = (...parts: Array<string | null | undefined>) => parts
  .map((part) => part?.trim() ?? '')
  .filter(Boolean)
  .join(' ')
  .replace(/\s+/g, ' ')
  .trim()
const limitChatMessage = (value: string) => {
  const normalized = value.replace(/\s+/g, ' ').trim()
  if (normalized.length <= MAX_CHAT_MESSAGE_LENGTH) return normalized
  const hardLimit = normalized.slice(0, MAX_CHAT_MESSAGE_LENGTH)
  const lastBoundary = hardLimit.lastIndexOf(' ')
  return (lastBoundary >= Math.floor(MAX_CHAT_MESSAGE_LENGTH * 0.8)
    ? hardLimit.slice(0, lastBoundary)
    : hardLimit).trim()
}

type ViewTurn = {
  id: string
  role: 'user' | 'agent'
  text: string
  response?: AgentChatResponse
}

type BrowserSpeechResult = {
  readonly isFinal: boolean
  readonly 0?: { readonly transcript: string }
}
type BrowserSpeechResultEvent = {
  readonly resultIndex: number
  readonly results: ArrayLike<BrowserSpeechResult>
}
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
type SpeechSession = {
  attempt: number
  constructor: BrowserSpeechRecognitionConstructor
  audioTrack: MediaStreamTrack
  baseDraft: string
  accumulatedFinal: string
  latestCycleFinal: string
  latestCycleInterim: string
  latestTranscript: string
  heardSpeech: boolean
  stopRequested: boolean
  cancelled: boolean
  submitted: boolean
  fatalError: boolean
  silenceTimer: number | null
  maxTimer: number | null
  restartTimer: number | null
}

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

function detailToViewTurns(detail: AgentConversationDetail): ViewTurn[] {
  const restored: ViewTurn[] = []
  detail.turns.forEach((turn) => {
    restored.push({ id: `${turn.turn_id}-u`, role: 'user', text: turn.user_message })
    restored.push({
      id: turn.turn_id,
      role: 'agent',
      text: turn.assistant_reply,
      response: turn.response,
    })
  })
  return restored
}

function detailProposal(detail: AgentConversationDetail): PurchaseProposal | null {
  return [...detail.turns]
    .reverse()
    .find((turn) => turn.response.purchase_proposal)
    ?.response.purchase_proposal ?? null
}

export default function AgentWorkspace({
  profile,
  sessionChecked,
  onSignIn,
  onClose,
}: {
  profile: AccountProfile | null
  sessionChecked: boolean
  onSignIn: () => void
  onClose: () => void
}) {
  const [conversations, setConversations] = useState<AgentConversationSummary[]>([])
  const [current, setCurrent] = useState<AgentConversationDetail | null>(null)
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null)
  const [detailLoadingId, setDetailLoadingId] = useState<string | null>(null)
  const [turns, setTurns] = useState<ViewTurn[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [proposal, setProposal] = useState<PurchaseProposal | null>(null)
  const [listening, setListening] = useState(false)
  const [requestingMic, setRequestingMic] = useState(false)

  const endRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const detailCacheRef = useRef(new Map<string, AgentConversationDetail>())
  const conversationVersionRef = useRef(new Map<string, number>())
  const checkoutProposalRef = useRef(new Map<string, PurchaseProposal>())
  const detailControllerRef = useRef<AbortController | null>(null)
  const detailGenerationRef = useRef(0)
  const selectedConversationRef = useRef<string | null>(null)
  const speechRef = useRef<BrowserSpeechRecognition | null>(null)
  const speechSessionRef = useRef<SpeechSession | null>(null)
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

  function clearSpeechTimers(session: SpeechSession) {
    if (session.silenceTimer !== null) window.clearTimeout(session.silenceTimer)
    if (session.maxTimer !== null) window.clearTimeout(session.maxTimer)
    if (session.restartTimer !== null) window.clearTimeout(session.restartTimer)
    session.silenceTimer = null
    session.maxTimer = null
    session.restartTimer = null
  }

  function stopSpeechRecognition(updateState = true) {
    speechAttemptRef.current += 1
    const session = speechSessionRef.current
    if (session) {
      session.cancelled = true
      clearSpeechTimers(session)
      speechSessionRef.current = null
    }
    const recognition = speechRef.current
    if (recognition) {
      recognition.onresult = null
      recognition.onerror = null
      recognition.onend = null
      try {
        recognition.abort()
      } catch {
        // Recognition was already inactive.
      }
      speechRef.current = null
    }
    releaseMicrophoneStream()
    if (updateState) {
      setListening(false)
      setRequestingMic(false)
    }
  }

  function resetVisibleSession() {
    stopSpeechRecognition()
    setTurns([])
    setDraft('')
    setProposal(null)
    setError(null)
    focusComposer()
  }

  function invalidateDetailCache(conversationId?: string) {
    if (!conversationId) {
      detailCacheRef.current.clear()
      return
    }
    for (const key of detailCacheRef.current.keys()) {
      if (key.startsWith(`${conversationId}:`)) detailCacheRef.current.delete(key)
    }
  }

  function cacheDetail(detail: AgentConversationDetail): boolean {
    const appliedVersion = conversationVersionRef.current.get(detail.conversation_id)
    if (appliedVersion !== undefined && detail.version < appliedVersion) return false
    invalidateDetailCache(detail.conversation_id)
    detailCacheRef.current.set(
      detailCacheKey(detail.conversation_id, detail.version),
      detail,
    )
    conversationVersionRef.current.set(detail.conversation_id, detail.version)
    return true
  }

  function applyDetail(detail: AgentConversationDetail) {
    const appliedVersion = conversationVersionRef.current.get(detail.conversation_id)
    if (appliedVersion !== undefined && detail.version < appliedVersion) return
    conversationVersionRef.current.set(detail.conversation_id, detail.version)
    setCurrent(detail)
    setTurns(detailToViewTurns(detail))
    setProposal(
      checkoutProposalRef.current.get(detail.conversation_id) ?? detailProposal(detail),
    )
  }

  async function openConversation(conversationId: string, expectedVersion?: number) {
    stopSpeechRecognition()
    const generation = detailGenerationRef.current + 1
    detailGenerationRef.current = generation
    detailControllerRef.current?.abort()
    const controller = new AbortController()
    detailControllerRef.current = controller
    selectedConversationRef.current = conversationId
    setSelectedConversationId(conversationId)
    setDraft('')
    setError(null)

    const summary = conversations.find((item) => item.conversation_id === conversationId)
    const requestedVersion = expectedVersion ?? summary?.version
    const appliedVersion = conversationVersionRef.current.get(conversationId)
    const version = requestedVersion === undefined
      ? appliedVersion
      : Math.max(requestedVersion, appliedVersion ?? requestedVersion)
    if (version !== undefined) {
      conversationVersionRef.current.set(conversationId, version)
    }
    const cached = version === undefined
      ? undefined
      : detailCacheRef.current.get(detailCacheKey(conversationId, version))
    if (cached) {
      applyDetail(cached)
      setDetailLoadingId(null)
    } else {
      setCurrent(null)
      setTurns([])
      setProposal(null)
      setDetailLoadingId(conversationId)
    }

    try {
      const detail = await fetchAgentConversation(conversationId, controller.signal)
      if (!cacheDetail(detail)) return
      if (
        detailGenerationRef.current !== generation
        || selectedConversationRef.current !== conversationId
      ) return
      applyDetail(detail)
      setConversations((items) => items.map((item) => (
        item.conversation_id === detail.conversation_id
          ? { ...item, ...detail, turns: undefined } as AgentConversationSummary
          : item
      )))
    } catch (reason) {
      if (
        !(reason instanceof DOMException && reason.name === 'AbortError')
        && detailGenerationRef.current === generation
        && selectedConversationRef.current === conversationId
      ) {
        setError(reason instanceof Error ? reason.message : 'Conversation could not be opened.')
      }
    } finally {
      if (
        detailGenerationRef.current === generation
        && selectedConversationRef.current === conversationId
      ) setDetailLoadingId(null)
    }
  }

  async function refreshConversationCache(conversationId: string) {
    try {
      const detail = await fetchAgentConversation(conversationId)
      cacheDetail(detail)
    } catch {
      // The visible optimistic turn remains usable; a future selection will refresh again.
    }
  }

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns, busy, detailLoadingId])

  useEffect(() => {
    if (!busy && detailLoadingId === null) focusComposer()
  }, [busy, detailLoadingId, current?.conversation_id])

  useEffect(() => {
    detailGenerationRef.current += 1
    detailControllerRef.current?.abort()
    detailControllerRef.current = null
    invalidateDetailCache()
    conversationVersionRef.current.clear()
    checkoutProposalRef.current.clear()
    selectedConversationRef.current = null
    setSelectedConversationId(null)
    setDetailLoadingId(null)

    if (!profile) {
      setConversations([])
      setCurrent(null)
      resetVisibleSession()
      return
    }

    const controller = new AbortController()
    void fetchAgentConversations(controller.signal).then((result) => {
      result.items.forEach((item) => {
        conversationVersionRef.current.set(item.conversation_id, item.version)
      })
      setConversations(result.items)
      const first = result.items[0]
      if (first) void openConversation(first.conversation_id, first.version)
      else {
        setCurrent(null)
        resetVisibleSession()
      }
    }).catch((reason: unknown) => {
      if (!(reason instanceof DOMException && reason.name === 'AbortError')) {
        setError(reason instanceof Error ? reason.message : 'Conversations are unavailable.')
      }
    })
    return () => {
      controller.abort()
      detailGenerationRef.current += 1
      detailControllerRef.current?.abort()
    }
  }, [profile?.id, profile?.display_name])

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
    }).catch(() => {
      microphonePermissionRef.current = 'unknown'
    })
    return () => {
      active = false
      if (permissionStatus) permissionStatus.onchange = null
    }
  }, [])

  useEffect(() => () => {
    stopSpeechRecognition(false)
  }, [])

  async function newConversation() {
    if (busy) return
    detailGenerationRef.current += 1
    detailControllerRef.current?.abort()
    stopSpeechRecognition()
    if (!profile) {
      selectedConversationRef.current = null
      setSelectedConversationId(null)
      setCurrent(null)
      resetVisibleSession()
      return
    }

    setBusy(true)
    setError(null)
    try {
      const created = await createAgentConversation()
      const detail: AgentConversationDetail = { ...created, turns: [] }
      cacheDetail(detail)
      selectedConversationRef.current = created.conversation_id
      setSelectedConversationId(created.conversation_id)
      setConversations((items) => [
        created,
        ...items.filter((item) => item.conversation_id !== created.conversation_id),
      ])
      setCurrent(detail)
      setTurns([])
      setDraft('')
      setProposal(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Conversation could not be created.')
    } finally {
      setBusy(false)
    }
  }

  async function closeCurrent() {
    const conversationId = selectedConversationId ?? current?.conversation_id
    if (!conversationId || busy) return
    setBusy(true)
    setError(null)
    detailGenerationRef.current += 1
    detailControllerRef.current?.abort()
    stopSpeechRecognition()
    try {
      await closeAgentConversation(conversationId)
      invalidateDetailCache(conversationId)
      conversationVersionRef.current.delete(conversationId)
      checkoutProposalRef.current.delete(conversationId)
      const remaining = conversations.filter(
        (item) => item.conversation_id !== conversationId,
      )
      setConversations(remaining)
      selectedConversationRef.current = null
      setSelectedConversationId(null)
      setCurrent(null)
      setTurns([])
      setProposal(null)
      const next = remaining[0]
      if (next) void openConversation(next.conversation_id, next.version)
      else focusComposer()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Conversation could not be closed.')
    } finally {
      setBusy(false)
    }
  }

  async function submit(message: string, extra: Partial<AgentChatRequest> = {}) {
    const text = message.trim()
    if (!text || busy) return
    if (text.length > MAX_CHAT_MESSAGE_LENGTH) {
      setError(`Messages can be at most ${MAX_CHAT_MESSAGE_LENGTH.toLocaleString()} characters.`)
      return
    }
    if (profile && detailLoadingId !== null) {
      setError('Wait for this session to finish loading before sending a message.')
      return
    }
    detailGenerationRef.current += 1
    detailControllerRef.current?.abort()
    detailControllerRef.current = null
    setDetailLoadingId(null)
    if (speechSessionRef.current) stopSpeechRecognition()
    const activeConversation = current
    const clientTurnId = id()
    setTurns((items) => [
      ...items,
      { id: `${clientTurnId}-u`, role: 'user', text },
    ])
    setDraft('')
    setProposal(null)
    setBusy(true)
    setError(null)
    const request: AgentChatRequest = {
      message: text,
      limit: 4,
      client_turn_id: clientTurnId,
      ...extra,
    }
    if (profile && activeConversation) {
      request.conversation_id = activeConversation.conversation_id
      request.expected_conversation_version = activeConversation.version
    }

    try {
      const response = await sendAgentChat(request)
      setTurns((items) => [
        ...items,
        {
          id: response.turn_id ?? id(),
          role: 'agent',
          text: response.reply,
          response,
        },
      ])
      setProposal(response.purchase_proposal ?? null)
      if (profile && response.conversation_id) {
        const conversationId = response.conversation_id
        const nextVersion = response.conversation_version
          ?? activeConversation?.version
          ?? 1
        conversationVersionRef.current.set(conversationId, nextVersion)
        invalidateDetailCache(conversationId)
        selectedConversationRef.current = conversationId
        setSelectedConversationId(conversationId)
        setCurrent((value) => {
          if (value?.conversation_id === conversationId) {
            return {
              ...value,
              version: nextVersion,
              turn_count: value.turn_count + 1,
              last_message_preview: text.slice(0, 240),
            }
          }
          return value
        })
        setConversations((items) => {
          const existing = items.find((item) => item.conversation_id === conversationId)
          if (!existing) return items
          const updated = {
            ...existing,
            version: nextVersion,
            turn_count: existing.turn_count + 1,
            last_message_preview: text.slice(0, 240),
          }
          return [
            updated,
            ...items.filter((item) => item.conversation_id !== conversationId),
          ]
        })
        void refreshConversationCache(conversationId)
        void fetchAgentConversations().then((list) => {
          list.items.forEach((item) => {
            const applied = conversationVersionRef.current.get(item.conversation_id) ?? 0
            conversationVersionRef.current.set(
              item.conversation_id,
              Math.max(applied, item.version),
            )
          })
          setConversations(list.items)
        }).catch(() => {
          // The optimistic summary remains available.
        })
      }
    } catch (reason) {
      if (
        reason instanceof ApiError
        && reason.code === 'CONVERSATION_CHANGED'
        && activeConversation
      ) {
        await openConversation(
          activeConversation.conversation_id,
          conversations.find(
            (item) => item.conversation_id === activeConversation.conversation_id,
          )?.version,
        )
        setError('Conversation changed in another tab. It was reloaded; send again when ready.')
      } else {
        setError(reason instanceof Error ? reason.message : 'The Agent could not complete this turn.')
      }
    } finally {
      setBusy(false)
    }
  }

  async function readMicrophonePermission(): Promise<MicrophonePermissionState> {
    if (!navigator.permissions?.query) return 'unknown'
    try {
      const status = await navigator.permissions.query({
        name: 'microphone' as PermissionName,
      })
      microphonePermissionRef.current = status.state
      return status.state
    } catch {
      return 'unknown'
    }
  }

  function finishSpeechFromEnd(session: SpeechSession) {
    clearSpeechTimers(session)
    speechRef.current = null
    speechSessionRef.current = null
    releaseMicrophoneStream()
    setListening(false)
    setRequestingMic(false)
    const transcript = limitChatMessage(session.latestTranscript)
    setDraft(transcript)
    if (
      !session.cancelled
      && !session.fatalError
      && session.heardSpeech
      && transcript
      && !session.submitted
    ) {
      session.submitted = true
      void submit(transcript)
    }
  }

  function startSpeechRecognitionCycle(session: SpeechSession) {
    if (
      speechAttemptRef.current !== session.attempt
      || speechSessionRef.current !== session
      || session.cancelled
    ) return

    const recognition = new session.constructor()
    const cycleResults = new Map<number, { text: string; final: boolean }>()
    recognition.lang = 'en-IN'
    recognition.continuous = true
    recognition.interimResults = true
    recognition.onresult = (event) => {
      if (
        speechAttemptRef.current !== session.attempt
        || speechRef.current !== recognition
        || session.cancelled
      ) return
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index]
        const text = result?.[0]?.transcript?.trim() ?? ''
        if (text) cycleResults.set(index, { text, final: result.isFinal })
        else cycleResults.delete(index)
      }
      const ordered = [...cycleResults.entries()].sort(([left], [right]) => left - right)
      session.latestCycleFinal = joinSpeech(
        ...ordered.filter(([, value]) => value.final).map(([, value]) => value.text),
      )
      session.latestCycleInterim = joinSpeech(
        ...ordered.filter(([, value]) => !value.final).map(([, value]) => value.text),
      )
      const heardNow = joinSpeech(session.latestCycleFinal, session.latestCycleInterim)
      if (heardNow) session.heardSpeech = true
      const completeTranscript = joinSpeech(
        session.baseDraft,
        session.accumulatedFinal,
        session.latestCycleFinal,
        session.latestCycleInterim,
      )
      const reachedLimit = completeTranscript.length > MAX_CHAT_MESSAGE_LENGTH
      session.latestTranscript = limitChatMessage(completeTranscript)
      setDraft(session.latestTranscript)
      if (reachedLimit) {
        setError(
          `Voice message reached the ${MAX_CHAT_MESSAGE_LENGTH.toLocaleString()}-character limit and was stopped.`,
        )
        requestGracefulSpeechStop(session)
        return
      }
      if (session.heardSpeech && !session.stopRequested) {
        if (session.silenceTimer !== null) window.clearTimeout(session.silenceTimer)
        session.silenceTimer = window.setTimeout(() => {
          requestGracefulSpeechStop(session)
        }, 3_000)
      }
    }
    recognition.onerror = (event) => {
      if (
        speechAttemptRef.current !== session.attempt
        || speechRef.current !== recognition
        || session.cancelled
      ) return
      if (event.error === 'aborted' || event.error === 'no-speech') return
      session.fatalError = true
      session.stopRequested = true
      if (event.error === 'not-allowed') {
        void readMicrophonePermission().then((permission) => {
          if (speechAttemptRef.current === session.attempt && !session.cancelled) {
            setError(speechErrorMessage(event.error, permission))
          }
        })
      } else {
        setError(speechErrorMessage(event.error, microphonePermissionRef.current))
      }
    }
    recognition.onend = () => {
      if (
        speechAttemptRef.current !== session.attempt
        || speechRef.current !== recognition
        || session.cancelled
      ) return
      speechRef.current = null
      session.accumulatedFinal = limitChatMessage(joinSpeech(
        session.accumulatedFinal,
        session.latestCycleFinal,
        session.latestCycleInterim,
      ))
      session.latestCycleFinal = ''
      session.latestCycleInterim = ''
      session.latestTranscript = limitChatMessage(
        joinSpeech(session.baseDraft, session.accumulatedFinal),
      )
      setDraft(session.latestTranscript)

      if (!session.stopRequested && !session.fatalError) {
        session.restartTimer = window.setTimeout(() => {
          session.restartTimer = null
          startSpeechRecognitionCycle(session)
        }, 120)
        return
      }
      finishSpeechFromEnd(session)
    }

    speechRef.current = recognition
    setError(null)
    try {
      try {
        recognition.start(session.audioTrack)
      } catch (reason) {
        if (!(reason instanceof TypeError)) throw reason
        recognition.start()
      }
      setListening(true)
      if (session.stopRequested) {
        window.setTimeout(() => {
          if (speechRef.current === recognition) recognition.stop()
        }, 0)
      }
    } catch {
      recognition.onresult = null
      recognition.onerror = null
      recognition.onend = null
      if (speechRef.current === recognition) speechRef.current = null
      session.cancelled = true
      clearSpeechTimers(session)
      if (speechSessionRef.current === session) speechSessionRef.current = null
      releaseMicrophoneStream()
      setListening(false)
      setError('The browser could not start speech recognition from the microphone. Try again in the latest Chrome, or type your message.')
    }
  }

  function requestGracefulSpeechStop(session: SpeechSession) {
    if (
      session.cancelled
      || session.stopRequested
      || speechSessionRef.current !== session
    ) return
    session.stopRequested = true
    if (session.silenceTimer !== null) window.clearTimeout(session.silenceTimer)
    if (session.maxTimer !== null) window.clearTimeout(session.maxTimer)
    if (session.restartTimer !== null) window.clearTimeout(session.restartTimer)
    session.silenceTimer = null
    session.maxTimer = null
    session.restartTimer = null
    const recognition = speechRef.current
    if (recognition) {
      try {
        recognition.stop()
      } catch {
        startSpeechRecognitionCycle(session)
      }
    } else {
      startSpeechRecognitionCycle(session)
    }
  }

  async function requestMicrophoneAndStart(
    SpeechRecognition: BrowserSpeechRecognitionConstructor,
  ) {
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
      const baseDraft = limitChatMessage(draft)
      const session: SpeechSession = {
        attempt,
        constructor: SpeechRecognition,
        audioTrack,
        baseDraft,
        accumulatedFinal: '',
        latestCycleFinal: '',
        latestCycleInterim: '',
        latestTranscript: baseDraft,
        heardSpeech: false,
        stopRequested: false,
        cancelled: false,
        submitted: false,
        fatalError: false,
        silenceTimer: null,
        maxTimer: null,
        restartTimer: null,
      }
      speechSessionRef.current = session
      session.maxTimer = window.setTimeout(() => {
        requestGracefulSpeechStop(session)
      }, 60_000)
      setRequestingMic(false)
      startSpeechRecognitionCycle(session)
    } catch (reason) {
      if (speechAttemptRef.current !== attempt) return
      if (
        reason instanceof DOMException
        && (reason.name === 'NotAllowedError' || reason.name === 'SecurityError')
      ) {
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
    const session = speechSessionRef.current
    if (session) {
      requestGracefulSpeechStop(session)
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
    const SpeechRecognition = speechWindow.SpeechRecognition
      ?? speechWindow.webkitSpeechRecognition
    if (!SpeechRecognition) {
      setError('Speech recognition is not supported in this browser. Try the latest Chrome or Edge.')
      return
    }
    if (draft.trim().length >= MAX_CHAT_MESSAGE_LENGTH) {
      setError(
        `The draft already reached the ${MAX_CHAT_MESSAGE_LENGTH.toLocaleString()}-character limit. Send or shorten it before adding voice input.`,
      )
      return
    }
    void requestMicrophoneAndStart(SpeechRecognition)
  }

  function recommendationCards(response: AgentChatResponse) {
    return <>
      {response.clarification ? <div className="clarification-options">
        {response.clarification.options.map((option) => <button
          type="button"
          key={option.product_id}
          onClick={() => void submit(option.label, {
            selected_product_id: option.product_id,
          })}
        >
          {option.label}
        </button>)}
      </div> : null}
      <div className="agent-recommendations">
        {response.recommendations.map((item) => <article
          className="agent-recommendation"
          key={item.product.id}
        >
          <div>
            {response.exact_match && response.focus_product_id === item.product.id
              ? <span className="exact-badge">Exact catalogue match</span>
              : null}
            <h3>{item.product.title}</h3>
            <p>{item.reasons.slice(0, 2).join(' · ')}</p>
            <strong>{money(item.product.offer_price_paise)}</strong>
          </div>
          <small>{item.score}/100</small>
        </article>)}
      </div>
      {response.outcome === 'NO_MATCH' || response.outcome === 'BLOCKED'
        ? <div className="agent-recovery">
          {(response.remaining_replans ?? 0) > 0 ? <>
            <button type="button" onClick={() => void submit('Show me a cheaper option')}>
              Try cheaper
            </button>
            <button type="button" onClick={() => void submit('Show me another option')}>
              Try another
            </button>
          </> : <button type="button" onClick={() => void newConversation()}>
            Start a fresh session
          </button>}
        </div>
        : null}
    </>
  }

  const detailIsLoading = detailLoadingId !== null
    && detailLoadingId === selectedConversationId
  return <div
    className="agent-modal-backdrop"
    role="presentation"
    onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}
  >
    <main
      className="agent-workspace agent-modal"
      role="dialog"
      aria-modal="true"
      aria-label="Shopy Agent"
    >
      <button
        className="agent-modal-close"
        type="button"
        onClick={onClose}
        aria-label="Close Shopy Agent"
      >×</button>
      <header className="agent-workspace-head">
        <div><span className="section-label">GOVERNED AI BUYER</span><h1>Shopy Agent</h1></div>
        <button
          className="agent-new-button"
          type="button"
          onClick={() => void newConversation()}
          disabled={busy}
        >+ New session</button>
      </header>
      <div className="agent-layout">
        <aside className="agent-sidebar">
          <div className="agent-panel-title">
            <span>Sessions</span>
            {selectedConversationId ? <button
              type="button"
              className="agent-text-button"
              onClick={() => void closeCurrent()}
              disabled={busy}
            >Close</button> : null}
          </div>
          {!profile && sessionChecked ? <div className="agent-empty-copy">
            <p>Guest search works, but sign in to save sessions and buy.</p>
            <button type="button" onClick={onSignIn}>Sign in</button>
          </div> : <div className="conversation-list">
            {conversations.map((conversation) => <button
              type="button"
              className={
                selectedConversationId === conversation.conversation_id ? 'active' : ''
              }
              key={conversation.conversation_id}
              onClick={() => void openConversation(
                conversation.conversation_id,
                conversation.version,
              )}
              disabled={busy}
            >
              <strong>{conversation.title}</strong>
              <small>{conversation.last_message_preview ?? 'New conversation'}</small>
            </button>)}
          </div>}
        </aside>
        <section className="agent-thread">
          <div className="agent-messages-full" aria-live="polite">
            {detailIsLoading ? <div className="agent-session-loading">
              Loading this session…
            </div> : null}
            {!detailIsLoading && turns.length === 0 ? <div className="agent-turn">
              <div className="agent-turn-bubble">Hello, I am Shopy. How can I help you?</div>
            </div> : null}
            {turns.map((turn) => <div
              className={`agent-turn ${turn.role}`}
              key={turn.id}
            >
              <div className="agent-turn-bubble">{turn.text}</div>
              {turn.response ? recommendationCards(turn.response) : null}
            </div>)}
            {proposal ? <div className="agent-inline-checkout">
              <AgentCheckout
                key={proposal.proposal_id}
                proposal={proposal}
                signedIn={profile !== null}
                onSignIn={onSignIn}
                onRunChange={() => {
                  if (current) invalidateDetailCache(current.conversation_id)
                }}
                onProposalCreated={(nextProposal) => {
                  setProposal(nextProposal)
                  if (current) {
                    checkoutProposalRef.current.set(
                      current.conversation_id,
                      nextProposal,
                    )
                  }
                }}
              />
            </div> : null}
            {busy ? <div className="agent-typing-full">
              Shopy is working on your request…
            </div> : null}
            <div ref={endRef} />
          </div>
          <form
            className="agent-composer"
            onSubmit={(event) => {
              event.preventDefault()
              void submit(draft)
            }}
          >
            <input
              ref={inputRef}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              maxLength={MAX_CHAT_MESSAGE_LENGTH}
              placeholder="Ask for a product or say cheaper / another…"
            />
            <button
              className={`agent-mic${listening ? ' listening' : ''}${requestingMic ? ' requesting' : ''}`}
              type="button"
              onClick={toggleSpeechRecognition}
              disabled={busy || requestingMic || detailIsLoading}
              aria-label={
                requestingMic
                  ? 'Requesting microphone access'
                  : listening
                    ? 'Stop and send voice message'
                    : 'Speak your message'
              }
              aria-pressed={listening}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <rect x="9" y="3" width="6" height="11" rx="3" />
                <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3M9 21h6" />
              </svg>
            </button>
            <button
              className="agent-send"
              type="submit"
              disabled={!draft.trim() || busy || detailIsLoading}
              aria-label="Send message"
            >→</button>
          </form>
          {error ? <p
            className="agent-inline-error"
            style={{ padding: '0 18px 16px' }}
          >{error}</p> : null}
        </section>
      </div>
    </main>
  </div>
}

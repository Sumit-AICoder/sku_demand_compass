import React, { useEffect, useRef, useState } from 'react'
import { useStore } from '../lib/store'
import ChatBlocks, { Block } from './ChatBlocks'
import Markdown from './Markdown'

const BASE = '/api'
const SESSION_KEY = 'sonalika.chat.session'

interface Msg { role: 'user' | 'assistant'; text: string; trace?: any[]; blocks?: Block[] }
interface Fact { text: string; at: number }

/**
 * Ask-the-data chat, with memory.
 *
 * Three things persist: the conversation (so follow-ups resolve and a reload resumes
 * mid-thread), durable facts about the user (territory, priorities), and the session id
 * in localStorage. Clearing the thread and forgetting the person are separate actions —
 * wiping someone's territory because they wanted a clean thread would be a poor surprise.
 */
export default function Chat({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { view, sku, category } = useStore()
  const [sessionId, setSessionId] = useState<string | null>(
    () => { try { return localStorage.getItem(SESSION_KEY) } catch { return null } })
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [facts, setFacts] = useState<Fact[]>([])
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [sugg, setSugg] = useState<string[]>([])
  const [provider, setProvider] = useState<string>('')
  const [showTrace, setShowTrace] = useState<number | null>(null)
  const [showMem, setShowMem] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)

  // Resume the thread on open, rather than greeting a returning user as a stranger.
  useEffect(() => {
    if (!open) return
    fetch(`${BASE}/chat/suggestions`).then(r => r.json())
      .then(d => { setSugg(d.suggestions); setProvider(d.provider) }).catch(() => {})
    fetch(`${BASE}/chat/session${sessionId ? `?session_id=${sessionId}` : ''}`)
      .then(r => r.json())
      .then(d => {
        setSessionId(d.session_id)
        try { localStorage.setItem(SESSION_KEY, d.session_id) } catch {}
        setMsgs((d.turns ?? []).map((t: any) => ({
          role: t.role, text: t.text, trace: t.trace, blocks: t.blocks })))
        setFacts(d.facts ?? [])
      }).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [msgs, busy])

  const ask = async (text: string) => {
    if (!text.trim() || busy) return
    setMsgs(m => [...m, { role: 'user', text }])
    setQ(''); setBusy(true)
    try {
      const r = await fetch(`${BASE}/chat`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: text, session_id: sessionId,
          // what they're looking at, so an ambiguous question lands in the right frame
          context: { view, product: sku, category },
        }),
      })
      const d = await r.json()
      if (d.session_id && d.session_id !== sessionId) {
        setSessionId(d.session_id)
        try { localStorage.setItem(SESSION_KEY, d.session_id) } catch {}
      }
      setMsgs(m => [...m, { role: 'assistant', text: d.answer, trace: d.trace,
                            blocks: d.blocks }])
      if (d.facts) setFacts(d.facts)
    } catch (e) {
      setMsgs(m => [...m, { role: 'assistant', text: `Sorry — ${e}` }])
    } finally { setBusy(false) }
  }

  const newThread = async () => {
    const r = await fetch(`${BASE}/chat/session/new${sessionId ? `?session_id=${sessionId}` : ''}`,
                          { method: 'POST' })
    const d = await r.json()
    setSessionId(d.session_id); setMsgs([]); setFacts(d.facts ?? [])
    try { localStorage.setItem(SESSION_KEY, d.session_id) } catch {}
  }

  const forget = async (index?: number) => {
    if (!sessionId) return
    const url = `${BASE}/chat/memory?session_id=${sessionId}` +
                (index !== undefined ? `&index=${index}` : '')
    const d = await (await fetch(url, { method: 'DELETE' })).json()
    setFacts(d.facts ?? [])
  }

  if (!open) return null
  return (
    <>
      <div className="chat-scrim" onClick={onClose} />
      <aside className="chat">
        <header className="chat-head">
          <div>
            <b>Ask the data</b>
            <div className="muted" style={{ fontSize: 11 }}>
              {provider === 'azure' ? 'GPT-4.1 · answers from live queries'
                : provider === 'anthropic' ? 'Claude · answers from live queries'
                : 'Keyword mode — add an API key for full natural language'}
              {msgs.length > 0 && ' · remembers this thread'}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button className="link-btn" onClick={() => setShowMem(v => !v)}
                    title="What the assistant remembers about you">
              Memory{facts.length > 0 ? ` (${facts.length})` : ''}
            </button>
            <button className="link-btn" onClick={newThread}>New chat</button>
            <button className="link-btn" onClick={onClose}>close</button>
          </div>
        </header>

        {showMem && (
          <div className="mem-panel">
            <div className="mem-head">
              <span>What I remember about you</span>
              {facts.length > 0 &&
                <button className="link-btn" onClick={() => forget()}>forget all</button>}
            </div>
            {facts.length === 0
              ? <p className="note" style={{ margin: 0 }}>
                  Nothing yet. Tell me your territory or what you're prioritising —
                  &ldquo;I cover Punjab and we're pushing residue equipment&rdquo; — and
                  I'll apply it to every answer from then on.
                </p>
              : <ul className="mem-list">
                  {facts.map((f, i) => (
                    <li key={i}>
                      <span>{f.text}</span>
                      <button className="link-btn tiny" onClick={() => forget(i)}>forget</button>
                    </li>
                  ))}
                </ul>}
          </div>
        )}

        <div className="chat-body">
          {msgs.length === 0 && (
            <div className="chat-empty">
              <p className="note">
                Ask anything about the villages, products, districts or the data behind
                this dashboard. Every answer runs a real query — no guessing. Follow-up
                questions work: ask about Punjab, then just say &ldquo;what about
                Maharashtra?&rdquo;
              </p>
              <div className="chat-sugg">
                {sugg.map(s => (
                  <button key={s} className="chip-btn" onClick={() => ask(s)}>{s}</button>
                ))}
              </div>
            </div>
          )}
          {msgs.map((m, i) => (
            <div key={i} className={`bubble ${m.role}`}>
              {m.role === 'assistant'
                ? <Markdown text={m.text} />
                : <div className="btext">{m.text}</div>}
              <ChatBlocks blocks={m.blocks} />
              {m.trace && m.trace.length > 0 && (
                <>
                  <button className="link-btn tiny"
                          onClick={() => setShowTrace(showTrace === i ? null : i)}>
                    {showTrace === i ? 'hide how I got this' : 'how I got this'}
                  </button>
                  {showTrace === i && (
                    <pre className="trace">
                      {m.trace.map((t: any, j: number) =>
                        `${j + 1}. ${t.tool}(${JSON.stringify(t.input)})`).join('\n')}
                    </pre>
                  )}
                </>
              )}
            </div>
          ))}
          {busy && <div className="bubble assistant muted">thinking…</div>}
          <div ref={endRef} />
        </div>

        <form className="chat-input" onSubmit={e => { e.preventDefault(); ask(q) }}>
          <input value={q} onChange={e => setQ(e.target.value)}
                 placeholder="Ask about villages, products, districts…" />
          <button type="submit" disabled={busy || !q.trim()}>Ask</button>
        </form>
      </aside>
    </>
  )
}

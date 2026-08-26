import React from 'react'

/**
 * Minimal markdown renderer for chat prose.
 *
 * Not a full markdown library on purpose: the model's answers only ever use a handful
 * of constructs — paragraphs, **bold**, *italic*, `code`, and bullet/numbered lists.
 * A dependency-free renderer tuned to exactly that is smaller, has no supply-chain
 * surface, and cannot silently disagree with what the model was told to produce.
 * Actual tables are handled separately as structured blocks (see ChatBlocks) — this
 * component only ever sees prose.
 */

export type Line = { kind: 'p' | 'ul' | 'ol' | 'h'; text?: string; items?: string[] }

export function splitBlocks(text: string): Line[] {
  const lines = text.replace(/\r\n/g, '\n').split('\n')
  const blocks: Line[] = []
  let para: string[] = []
  let list: string[] = []
  let listKind: 'ul' | 'ol' | null = null

  const flushPara = () => {
    if (para.length) { blocks.push({ kind: 'p', text: para.join(' ').trim() }); para = [] }
  }
  const flushList = () => {
    if (list.length) { blocks.push({ kind: listKind!, items: list }); list = []; listKind = null }
  }

  for (const raw of lines) {
    const line = raw.trimEnd()
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line)
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line)
    const heading = /^\s*#{1,4}\s+(.*)$/.exec(line)

    if (!line.trim()) { flushPara(); flushList(); continue }
    if (heading) { flushPara(); flushList(); blocks.push({ kind: 'h', text: heading[1] }); continue }
    if (bullet) {
      flushPara()
      if (listKind === 'ol') flushList()
      listKind = 'ul'; list.push(bullet[1]); continue
    }
    if (numbered) {
      flushPara()
      if (listKind === 'ul') flushList()
      listKind = 'ol'; list.push(numbered[1]); continue
    }
    flushList()
    para.push(line.trim())
  }
  flushPara(); flushList()
  return blocks
}

/** Bold, italic and inline code — the only inline marks the model actually produces. */
function inline(text: string, keyBase: string): React.ReactNode[] {
  const out: React.ReactNode[] = []
  const re = /(\*\*([^*]+)\*\*|`([^`]+)`|\*([^*]+)\*)/g
  let last = 0, m: RegExpExecArray | null, i = 0
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index))
    if (m[2] !== undefined) out.push(<strong key={`${keyBase}-${i++}`}>{m[2]}</strong>)
    else if (m[3] !== undefined) out.push(<code key={`${keyBase}-${i++}`}>{m[3]}</code>)
    else if (m[4] !== undefined) out.push(<em key={`${keyBase}-${i++}`}>{m[4]}</em>)
    last = re.lastIndex
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

export default function Markdown({ text }: { text: string }) {
  const blocks = splitBlocks(text)
  if (blocks.length === 0) return null
  return (
    <div className="md">
      {blocks.map((b, i) => {
        if (b.kind === 'h') return <h4 key={i}>{inline(b.text!, `h${i}`)}</h4>
        if (b.kind === 'ul')
          return <ul key={i}>{b.items!.map((it, j) => <li key={j}>{inline(it, `u${i}-${j}`)}</li>)}</ul>
        if (b.kind === 'ol')
          return <ol key={i}>{b.items!.map((it, j) => <li key={j}>{inline(it, `o${i}-${j}`)}</li>)}</ol>
        return <p key={i}>{inline(b.text!, `p${i}`)}</p>
      })}
    </div>
  )
}

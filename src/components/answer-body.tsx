'use client';

import { Fragment, type ReactNode } from 'react';

import type { Citation } from '@/lib/types';

/**
 * Minimal, safe renderer for the assistant's answer.
 *
 * Deliberately NOT a markdown-to-HTML library with dangerouslySetInnerHTML.
 * The text is model output that was itself derived from user-uploaded
 * documents, so it is doubly untrusted; everything here is emitted as React
 * text nodes, which cannot introduce markup or script no matter what a
 * document contains. It covers the structure the model actually produces —
 * paragraphs, bullets, bold, inline code — and renders anything else as
 * plain text.
 *
 * Citation markers like [2] become buttons that open the passage the answer
 * drew on. A marker whose number has no matching source is rendered as plain
 * text — it points at nothing, so it must not look clickable.
 */
export function AnswerBody({
  content,
  citations,
  activeCitation,
  onCitationClick,
}: {
  content: string;
  citations: Citation[];
  /** Index of the citation currently open, so its marker can show as active. */
  activeCitation?: number | null;
  onCitationClick?: (index: number) => void;
}) {
  const valid = new Set(citations.map((c) => c.index));
  const blocks = splitBlocks(content);

  const marker = { valid, active: activeCitation ?? null, onClick: onCitationClick };

  return (
    <>
      {blocks.map((block, index) =>
        block.type === 'list' ? (
          <ul key={index}>
            {block.items.map((item, i) => (
              <li key={i}>{renderInline(item, marker)}</li>
            ))}
          </ul>
        ) : (
          <p key={index}>{renderInline(block.text, marker)}</p>
        ),
      )}
    </>
  );
}

interface MarkerContext {
  valid: Set<number>;
  active: number | null;
  onClick?: (index: number) => void;
}

type Block = { type: 'paragraph'; text: string } | { type: 'list'; items: string[] };

function splitBlocks(content: string): Block[] {
  const blocks: Block[] = [];
  let listBuffer: string[] = [];

  const flushList = () => {
    if (listBuffer.length > 0) {
      blocks.push({ type: 'list', items: listBuffer });
      listBuffer = [];
    }
  };

  for (const rawParagraph of content.split(/\n{2,}/)) {
    const paragraph = rawParagraph.trim();
    if (!paragraph) continue;

    const lines = paragraph.split('\n');
    const allBullets = lines.every((line) => /^\s*([-*•]|\d+[.)])\s+/.test(line));

    if (allBullets) {
      listBuffer.push(...lines.map((line) => line.replace(/^\s*([-*•]|\d+[.)])\s+/, '')));
      flushList();
    } else {
      flushList();
      blocks.push({ type: 'paragraph', text: paragraph.replace(/\n/g, ' ') });
    }
  }

  flushList();
  return blocks;
}

/** Handles **bold**, `code`, and [n] citation markers in one pass. */
function renderInline(text: string, marker: MarkerContext): ReactNode {
  const pattern = /(\*\*[^*]+\*\*)|(`[^`]+`)|(\[\d{1,2}\])/g;
  const nodes: ReactNode[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  let key = 0;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) {
      nodes.push(<Fragment key={key++}>{text.slice(cursor, match.index)}</Fragment>);
    }

    const token = match[0];

    if (token.startsWith('**')) {
      nodes.push(<strong key={key++}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith('`')) {
      nodes.push(<code key={key++}>{token.slice(1, -1)}</code>);
    } else {
      const index = Number(token.slice(1, -1));
      nodes.push(
        marker.valid.has(index) ? (
          <button
            key={key++}
            type="button"
            onClick={() => marker.onClick?.(index)}
            aria-expanded={marker.active === index}
            aria-label={`Show source ${index}`}
            // align-super rather than a <sup>: a real superscript shrinks the
            // hit area to a few pixels, and this has to be tappable.
            className={`mx-0.5 inline-flex h-4 min-w-4 select-none items-center justify-center rounded px-1 align-super text-[10px] font-semibold tabular-nums transition-colors ${
              marker.active === index
                ? 'bg-brand text-bg'
                : 'bg-brand-soft text-brand hover:bg-brand hover:text-bg'
            }`}
          >
            {index}
          </button>
        ) : (
          // A marker with no matching source is shown plainly rather than
          // styled as a citation — it points at nothing.
          <Fragment key={key++}>{token}</Fragment>
        ),
      );
    }

    cursor = match.index + token.length;
  }

  if (cursor < text.length) {
    nodes.push(<Fragment key={key++}>{text.slice(cursor)}</Fragment>);
  }

  return nodes;
}

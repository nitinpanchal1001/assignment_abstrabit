'use client';

import { useState } from 'react';

import type { RetrievalDebug, UsageStats } from '@/lib/types';

/**
 * Milliseconds as something a person reads at a glance.
 *
 * Sub-second stays in ms because "0.8s" hides the difference between a fast
 * answer and a very fast one; past a second, three or four digits of ms is
 * precision nobody wanted.
 */
function formatElapsed(ms: number): string {
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

/**
 * Retrieval-debug view.
 *
 * Exists to make isolation *checkable* rather than merely claimed: it shows
 * the workspace the vector query was scoped to and the workspace id stamped
 * on every chunk that came back. If those ever disagreed, it would be visible
 * here. It also reports which arm of the hybrid search found each chunk,
 * which is how you tell whether keyword matching is pulling its weight.
 */
export function RetrievalPanel({
  retrieval,
  usage,
}: {
  retrieval: RetrievalDebug;
  usage: UsageStats | null;
}) {
  const [open, setOpen] = useState(false);

  // Chunks owned by another workspace. Legitimate ONLY when they arrived
  // through an explicit grant, which the server labels with `sharedFrom`.
  const borrowed = retrieval.chunks.filter(
    (chunk) => chunk.workspaceId !== retrieval.workspaceId,
  );

  // A foreign chunk with no grant behind it is a leak. The distinction matters:
  // before sharing existed, any foreign chunk was a bug; now the bug is a
  // foreign chunk that cannot say who shared it.
  const foreign = borrowed.filter((chunk) => !chunk.sharedFrom);

  // Prefer the whole turn once it is known; until then the retrieval leg is
  // the only elapsed time that exists.
  const elapsedMs = usage?.latencyMs ?? retrieval.latencyMs;

  return (
    <div className="rounded-md border-0 bg-transparent">
      {/* Collapsed, this is a timing line and nothing else.
          Chunk counts, the fusion strategy and token totals used to sit here
          and made every answer end in a row of diagnostics — information the
          reader had not asked for, competing with the citations, which they
          had. The retrieval trace is still one click away, because proving
          which workspace an answer drew from is the point of having it; it is
          just no longer the default thing under every reply. */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label={open ? 'Hide retrieval details' : 'Show retrieval details'}
        className="-ml-1 flex items-center gap-1.5 rounded px-1 py-0.5 text-[11px] text-fg-subtle transition-colors hover:text-fg-muted"
      >
        <svg
          width="11"
          height="11"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
        >
          <circle cx="12" cy="12" r="9" />
          <path d="M12 7v5l3 2" />
        </svg>
        <span className="tabular-nums">{formatElapsed(elapsedMs)}</span>
        {foreign.length > 0 && (
          // The one diagnostic that is never hidden: if the tenant filter ever
          // failed, that must not be behind a disclosure.
          <span className="font-medium text-danger">· isolation check failed</span>
        )}
      </button>

      {open && (
        <div className="animate-rise mt-1.5 space-y-3 rounded-md border border-border-base bg-surface-2 px-3 py-3 text-xs">
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
            <dt className="text-fg-subtle">Scoped to workspace</dt>
            <dd className="font-mono text-[11px]">{retrieval.workspaceId}</dd>
            {/* Moved here from the collapsed summary, which is now timing only. */}
            <dt className="text-fg-subtle">Chunks used</dt>
            <dd>
              {retrieval.returnedCount} via {retrieval.strategy} · {retrieval.latencyMs}ms to
              retrieve
            </dd>
            <dt className="text-fg-subtle">Candidate pool</dt>
            <dd>{retrieval.candidateCount} before fusion</dd>
            <dt className="text-fg-subtle">Min similarity</dt>
            <dd>{retrieval.minSimilarity}</dd>
            {usage && (
              <>
                <dt className="text-fg-subtle">Model</dt>
                <dd className="font-mono text-[11px]">{usage.model}</dd>
                <dt className="text-fg-subtle">Tokens</dt>
                <dd>
                  {usage.inputTokens ?? '—'} in / {usage.outputTokens ?? '—'} out ·{' '}
                  {usage.latencyMs}ms · {usage.toolSteps} tool step
                  {usage.toolSteps === 1 ? '' : 's'}
                </dd>
              </>
            )}
          </dl>

          <p
            className={`rounded px-2 py-1.5 ${
              foreign.length === 0
                ? 'bg-success-soft text-success'
                : 'bg-danger-soft font-medium text-danger'
            }`}
          >
            {foreign.length > 0
              ? `${foreign.length} chunk(s) from another workspace were returned with no grant behind them. This is a bug.`
              : borrowed.length === 0
                ? 'All returned chunks belong to this workspace.'
                : `${borrowed.length} chunk(s) came from documents explicitly shared with this workspace; the rest are its own.`}
          </p>

          {retrieval.chunks.length === 0 ? (
            <p className="text-fg-muted">
              Nothing matched. The assistant should have said it doesn&rsquo;t know.
            </p>
          ) : (
            <ol className="space-y-2">
              {retrieval.chunks.map((chunk, i) => (
                <li key={chunk.chunkId} className="rounded border border-border-base bg-surface p-2">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="font-medium text-brand">[{i + 1}]</span>
                    <span className="font-medium">{chunk.filename}</span>
                    {chunk.sharedFrom && (
                      <span className="rounded bg-surface-2 px-1 text-[10px] text-fg-subtle">
                        shared from {chunk.sharedFrom}
                      </span>
                    )}
                    {chunk.section && <span className="text-fg-subtle">· {chunk.section}</span>}
                    <span className="text-fg-subtle">
                      · cos {chunk.similarity.toFixed(3)} · rrf {chunk.score.toFixed(4)}
                    </span>
                    <span className="text-fg-subtle">
                      ·{' '}
                      {chunk.vectorRank && chunk.keywordRank
                        ? `both (v${chunk.vectorRank}, k${chunk.keywordRank})`
                        : chunk.vectorRank
                          ? `vector only (v${chunk.vectorRank})`
                          : `keyword only (k${chunk.keywordRank})`}
                    </span>
                  </div>
                  <p className="mt-1.5 line-clamp-3 text-fg-muted">{chunk.preview}…</p>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </div>
  );
}

'use client';

import { useEffect, useRef, useState } from 'react';

import { ApiError, messageOf } from '@/lib/client/api';

import type {
  ChatMessageRecord,
  Citation,
  RetrievalDebug,
  ToolCallStatus,
  UsageStats,
} from '@/lib/types';

import { AnswerBody } from './answer-body';
import { RetrievalPanel } from './retrieval-panel';
import { ToolCallCard, type ToolEvent } from './tool-call-card';
import { Badge, Spinner } from './ui';

/**
 * The chat surface: sends a question, consumes the SSE stream, and renders
 * the answer with its citations, tool calls and retrieval trace.
 */

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  citations: Citation[];
  retrieval: RetrievalDebug | null;
  usage: UsageStats | null;
  tools: ToolEvent[];
  status: 'streaming' | 'complete' | 'failed';
  error?: string | null;
  /** True when re-sending the same question could plausibly succeed. */
  retryable?: boolean;
}

/** Server-sent `status` stages, in the order the agent emits them. */
const STAGE_LABEL: Record<string, string> = {
  retrieving: 'Searching this workspace…',
  thinking: 'Reading the passages…',
  tools: 'Running tools…',
  saving: 'Finishing up…',
};

const SUGGESTIONS = [
  'What are the key points in these documents?',
  'Summarise the main policy and save it as a task',
  'What is the capital of France?',
];

export function ChatPanel({
  workspaceId,
  workspaceName,
  conversationId: initialConversationId,
  initialMessages,
  documentCount,
}: {
  workspaceId: string;
  workspaceName: string;
  conversationId: string | null;
  initialMessages: ChatMessageRecord[];
  documentCount: number;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>(() =>
    initialMessages.map((row) => ({
      id: row.id,
      role: row.role,
      content: row.content,
      citations: row.citations ?? [],
      retrieval: row.retrieval ?? null,
      usage: row.usage ?? null,
      tools: [],
      status: row.status,
      error: row.errorMessage,
    })),
  );
  const [conversationId, setConversationId] = useState(initialConversationId);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState<string | null>(null);
  const [fatalError, setFatalError] = useState<string | null>(null);

  /**
   * Whether the viewport is pinned to the newest message.
   *
   * Without this the panel scrolled to the bottom on every token, so scrolling
   * up to re-read an earlier answer while a new one streamed yanked you back
   * down several times a second — the list became unreadable exactly when you
   * wanted to read it.
   */
  const [atBottom, setAtBottom] = useState(true);

  const scrollRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  /** Monotonic counter for optimistic row keys — no impure Date.now() at render time. */
  const localIdRef = useRef(0);
  /**
   * The assistant row currently being streamed into. Held in a ref because the
   * server replaces the optimistic id with a real one mid-stream, and every
   * later event in this handler must target whichever id is current.
   */
  const streamingIdRef = useRef<string>('');
  /** The last question sent, so a failed turn can be retried verbatim. */
  const lastQuestionRef = useRef<string | null>(null);

  useEffect(() => {
    if (!atBottom) return;
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, stage, atBottom]);

  // Abort any in-flight stream if the component unmounts (e.g. workspace switch).
  useEffect(() => () => abortRef.current?.abort(), []);

  function handleScroll(event: React.UIEvent<HTMLDivElement>) {
    const el = event.currentTarget;
    // 64px of slack: "close enough to the bottom" should survive the bounce of
    // a smooth scroll and the line-height jitter of streaming text.
    const next = el.scrollHeight - el.scrollTop - el.clientHeight < 64;
    // Only write when it flips — this fires on every scroll frame.
    setAtBottom((previous) => (previous === next ? previous : next));
  }

  function scrollToLatest() {
    setAtBottom(true);
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }

  /** Grow the composer with its content, up to roughly six lines. */
  function resizeComposer() {
    const el = composerRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 168)}px`;
  }

  function patchAssistant(id: string, update: Partial<ChatMessage>) {
    setMessages((previous) =>
      previous.map((message) => (message.id === id ? { ...message, ...update } : message)),
    );
  }

  async function send(question: string) {
    const trimmed = question.trim();
    if (!trimmed || busy) return;

    setBusy(true);
    setFatalError(null);
    setInput('');
    // Sending always returns you to the newest message: you asked for it, so
    // you want to see it arrive.
    setAtBottom(true);
    if (composerRef.current) composerRef.current.style.height = 'auto';

    lastQuestionRef.current = trimmed;

    const sequence = (localIdRef.current += 1);
    const tempUserId = `local-user-${sequence}`;
    const tempAssistantId = `local-assistant-${sequence}`;
    streamingIdRef.current = tempAssistantId;

    setMessages((previous) => [
      ...previous,
      {
        id: tempUserId,
        role: 'user',
        content: trimmed,
        citations: [],
        retrieval: null,
        usage: null,
        tools: [],
        status: 'complete',
      },
      {
        id: tempAssistantId,
        role: 'assistant',
        content: '',
        citations: [],
        retrieval: null,
        usage: null,
        tools: [],
        status: 'streaming',
      },
    ]);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ workspaceId, conversationId, message: trimmed }),
        signal: controller.signal,
      });

      if (!response.ok || !response.body) {
        // The error envelope is { error: { code, message, requestId } }, so the
        // message has to be read out of it — rendering `data.error` directly
        // would show "[object Object]".
        const body = await response.json().catch(() => null);
        const envelope = body?.error;
        throw new ApiError({
          message: envelope?.message ?? 'The assistant could not be reached. Please try again.',
          code: envelope?.code ?? 'internal',
          status: response.status,
          requestId: envelope?.requestId,
        });
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      // SSE frames are separated by a blank line and can be split across
      // network chunks, so the tail of the buffer is kept until it completes.
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split('\n\n');
        buffer = frames.pop() ?? '';

        for (const frame of frames) {
          const line = frame.trim();
          if (!line.startsWith('data:')) continue;

          const payload = line.slice(5).trim();
          if (payload === '[DONE]') continue;

          let event: Record<string, unknown>;
          try {
            event = JSON.parse(payload);
          } catch {
            continue;
          }

          switch (event.type) {
            case 'meta': {
              setConversationId(event.conversationId as string);
              const realId = event.messageId as string;
              const optimisticId = streamingIdRef.current;
              setMessages((previous) =>
                previous.map((m) => (m.id === optimisticId ? { ...m, id: realId } : m)),
              );
              streamingIdRef.current = realId;
              break;
            }

            case 'status':
              setStage(event.stage as string);
              break;

            case 'retrieval':
              // Deliberately does NOT set `citations`. This event carries every
              // chunk that was RETRIEVED; citations are the subset the finished
              // answer actually cited, which is only known at `done`. Setting
              // them here put a full source list under a refusal for the whole
              // duration of the stream, and it read as evidence for an answer
              // that was never given.
              patchAssistant(streamingIdRef.current, {
                retrieval: event.debug as RetrievalDebug,
              });
              break;

            case 'token':
              setMessages((previous) =>
                previous.map((m) =>
                  m.id === streamingIdRef.current ? { ...m, content: m.content + (event.text as string) } : m,
                ),
              );
              break;

            case 'tool_call':
              setMessages((previous) =>
                previous.map((m) =>
                  m.id === streamingIdRef.current
                    ? {
                        ...m,
                        tools: [
                          ...m.tools,
                          {
                            name: event.name as string,
                            args: event.args as Record<string, unknown>,
                            stepIndex: event.stepIndex as number,
                          },
                        ],
                      }
                    : m,
                ),
              );
              break;

            case 'tool_result':
              setMessages((previous) =>
                previous.map((m) =>
                  m.id === streamingIdRef.current
                    ? {
                        ...m,
                        tools: m.tools.map((tool) =>
                          tool.stepIndex === event.stepIndex
                            ? {
                                ...tool,
                                status: event.status as ToolCallStatus,
                                isError: event.isError as boolean,
                                latencyMs: event.latencyMs as number,
                              }
                            : tool,
                        ),
                      }
                    : m,
                ),
              );
              break;

            case 'done':
              patchAssistant(streamingIdRef.current, {
                status: 'complete',
                citations: event.citations as Citation[],
                usage: event.usage as UsageStats,
              });
              break;

            case 'error':
              patchAssistant(streamingIdRef.current, {
                status: 'failed',
                error: (event.message as string) ?? 'The answer could not be generated.',
                retryable: Boolean(event.retryable),
              });
              break;
          }
        }
      }
    } catch (caught) {
      // An abort is the component unmounting (e.g. a workspace switch), not a
      // failure — surfacing it as an error would be actively misleading.
      if (caught instanceof DOMException && caught.name === 'AbortError') return;

      const message = messageOf(caught, 'The request failed.');
      setFatalError(message);
      patchAssistant(streamingIdRef.current, {
        status: 'failed',
        error: message,
        // 0 is a network drop, 5xx is the server, and 429 is a rate limit —
        // whether ours (this workspace's share of the shared quota) or the
        // provider's. All three can succeed on a second attempt; a 4xx that is
        // not 429 means the request itself was wrong, so retrying it unchanged
        // would fail identically.
        retryable:
          caught instanceof ApiError
            ? caught.status === 0 || caught.status === 429 || caught.status >= 500
            : false,
      });
    } finally {
      setBusy(false);
      setStage(null);
      abortRef.current = null;
    }
  }

  return (
    <div className="flex h-screen flex-col">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border-base px-6 py-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Chat</h1>
          <p className="mt-0.5 text-sm text-fg-muted">
            Grounded in <span className="font-medium text-fg">{workspaceName}</span> ·{' '}
            {documentCount} {documentCount === 1 ? 'document' : 'documents'}
          </p>
        </div>
        {documentCount === 0 && <Badge tone="warning">No documents ingested yet</Badge>}
      </header>

      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="relative flex-1 overflow-y-auto px-6 py-6"
      >
        {messages.length === 0 ? (
          <div className="mx-auto max-w-2xl">
            <p className="text-sm text-fg-muted">
              Ask a question about the documents in this workspace. Answers cite their sources, and
              the assistant will say when the documents don&rsquo;t cover something.
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  disabled={busy}
                  onClick={() => void send(suggestion)}
                  className="rounded-full border border-border-base px-3 py-1.5 text-xs text-fg-muted transition-colors hover:border-brand hover:text-brand disabled:opacity-50"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="mx-auto max-w-2xl space-y-6">
            {messages.map((message) =>
              message.role === 'user' ? (
                <div key={message.id} className="flex justify-end">
                  <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-primary px-4 py-2.5 text-sm text-primary-fg">
                    {message.content}
                  </p>
                </div>
              ) : (
                <AssistantMessage
                  key={message.id}
                  message={message}
                  busy={busy}
                  // The ref is read inside the callback, not during render:
                  // refs are not render inputs, and reading one here would
                  // not re-render when it changed.
                  onRetry={() => {
                    const question = lastQuestionRef.current;
                    if (question) void send(question);
                  }}
                />
              ),
            )}

            {/* The stage line is the only feedback between sending and the
                first token, which is where the whole wait lives: retrieval
                embeds the query and searches two stores before the model has
                produced a single character. */}
            {busy && stage && (
              <p className="fade-in-delayed flex items-center gap-2 text-xs text-fg-subtle">
                <Spinner size={13} label={STAGE_LABEL[stage] ?? 'Working'} className="text-brand" />
                {STAGE_LABEL[stage] ?? 'Working…'}
              </p>
            )}
          </div>
        )}
      </div>

      <div className="relative border-t border-border-base px-6 py-4">
        {/* Escape hatch from the pinning rule above: once you scroll away,
            nothing drags you back until you ask. */}
        {!atBottom && messages.length > 0 && (
          <button
            type="button"
            onClick={scrollToLatest}
            className="animate-pop absolute -top-11 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-border-base bg-surface px-3 py-1.5 text-xs font-medium text-fg-muted shadow-lg shadow-black/5 transition-colors hover:text-fg"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden>
              <path d="M12 5v14M5 12l7 7 7-7" />
            </svg>
            Latest
          </button>
        )}

        <form
          onSubmit={(e) => {
            e.preventDefault();
            void send(input);
          }}
          className="mx-auto flex max-w-2xl items-end gap-2"
        >
          <textarea
            ref={composerRef}
            rows={1}
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              resizeComposer();
            }}
            onKeyDown={(e) => {
              // Enter sends, Shift+Enter starts a line. `isComposing` guards an
              // IME candidate window, where Enter means "accept this word" and
              // swallowing it would send a half-typed question.
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void send(input);
              }
            }}
            disabled={busy}
            maxLength={4000}
            placeholder={`Ask about ${workspaceName}…`}
            // max-h-42 is 168px, matching the clamp in resizeComposer(); this
            // is the backstop for before JS has run.
            className="max-h-42 flex-1 resize-none rounded-lg border border-border-base bg-surface px-3.5 py-2.5 text-sm leading-relaxed outline-none transition-colors focus:border-brand disabled:opacity-60"
          />
          <button
            type="submit"
            disabled={busy || !input.trim()}
            className="flex items-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-fg transition-colors hover:bg-primary-hover disabled:opacity-60"
          >
            {busy && <Spinner size={14} label="Generating answer" />}
            {busy ? 'Thinking…' : 'Send'}
          </button>
        </form>

        <p className="mx-auto mt-2 max-w-2xl text-[11px] text-fg-subtle">
          Enter to send · Shift+Enter for a new line
        </p>

        {fatalError && (
          <p role="alert" className="mx-auto mt-2 max-w-2xl text-xs text-danger">
            {fatalError}
          </p>
        )}
      </div>
    </div>
  );
}

function AssistantMessage({
  message,
  busy,
  onRetry,
}: {
  message: ChatMessage;
  busy: boolean;
  onRetry?: () => void;
}) {
  const streaming = message.status === 'streaming' && busy;

  /**
   * Which source is expanded, or null for none.
   *
   * One at a time: the point of opening a citation is to compare the sentence
   * you just read against the passage behind it, and a stack of open panels
   * pushes that sentence off the screen.
   */
  const [openCitation, setOpenCitation] = useState<number | null>(null);

  const active = message.citations.find((c) => c.index === openCitation) ?? null;

  function toggleCitation(index: number) {
    setOpenCitation((previous) => (previous === index ? null : index));
  }

  return (
    <div className="space-y-3">
      <div className="prose-answer text-sm">
        {message.content ? (
          <AnswerBody
            content={message.content}
            citations={message.citations}
            activeCitation={openCitation}
            onCitationClick={toggleCitation}
          />
        ) : (
          streaming && <span className="caret" />
        )}
        {streaming && message.content && <span className="caret" />}
      </div>

      {/* Sources appear only when the answer actually cited something. A
          refusal has no citations at all now (the server stopped attaching
          every retrieved chunk as a fallback), so nothing renders here and the
          refusal reads as what it is. */}
      {message.citations.length > 0 && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] font-medium uppercase tracking-wider text-fg-subtle">
              {message.citations.length === 1 ? 'Source' : 'Sources'}
            </span>
            {message.citations.map((citation) => {
              const open = citation.index === openCitation;
              return (
                <button
                  key={citation.chunkId}
                  type="button"
                  onClick={() => toggleCitation(citation.index)}
                  aria-expanded={open}
                  className={`group flex max-w-full items-center gap-1.5 rounded-full border py-1 pl-1 pr-2.5 text-xs transition-colors ${
                    open
                      ? 'border-brand bg-brand-soft text-brand'
                      : 'border-border-base text-fg-muted hover:border-brand hover:text-brand'
                  }`}
                >
                  <span
                    className={`grid size-4 shrink-0 place-items-center rounded-full text-[10px] font-semibold tabular-nums ${
                      open ? 'bg-brand text-bg' : 'bg-surface-2 text-fg-subtle group-hover:bg-brand-soft group-hover:text-brand'
                    }`}
                  >
                    {citation.index}
                  </span>
                  <span className="truncate">{citation.filename}</span>
                  {/* A shared source must never look local. The claim rests on
                      another workspace's document, and the reader is the only
                      one who can judge whether that is acceptable. */}
                  {citation.sharedFrom && (
                    <span className="shrink-0 rounded bg-surface-2 px-1 text-[10px] text-fg-subtle">
                      shared
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          {active && <SourcePanel citation={active} onClose={() => setOpenCitation(null)} />}
        </div>
      )}

      {message.tools.length > 0 && (
        <ul className="space-y-1.5">
          {message.tools.map((tool) => (
            <li key={tool.stepIndex}>
              <ToolCallCard tool={tool} stepCount={message.tools.length} />
            </li>
          ))}
        </ul>
      )}

      {message.status === 'failed' && message.error && (
        <div className="rounded-md bg-danger-soft px-3 py-2 text-xs text-danger">
          <p>{message.error}</p>
          {message.retryable && onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="mt-1.5 font-medium underline underline-offset-2 hover:no-underline"
            >
              Try again
            </button>
          )}
        </div>
      )}

      {message.retrieval && <RetrievalPanel retrieval={message.retrieval} usage={message.usage} />}
    </div>
  );
}

/**
 * The passage behind one citation.
 *
 * Shows the source text verbatim rather than a paraphrase, because the only
 * question this answers is "did the document really say that". The text is a
 * React text node inside a <blockquote> — never markdown, never HTML. It is
 * document content, which on this deployment includes a file that tries to
 * inject instructions, so it is rendered as inert prose by construction.
 */
function SourcePanel({ citation, onClose }: { citation: Citation; onClose: () => void }) {
  return (
    <div className="animate-rise overflow-hidden rounded-lg border border-brand/30 bg-surface-2">
      <div className="flex items-start gap-2 border-b border-border-base px-3 py-2">
        <span className="mt-px grid size-4 shrink-0 place-items-center rounded-full bg-brand text-[10px] font-semibold tabular-nums text-bg">
          {citation.index}
        </span>

        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-medium" title={citation.filename}>
            {citation.filename}
          </p>
          <p className="mt-0.5 text-[11px] text-fg-subtle">
            {citation.section ? `${citation.section} · ` : ''}
            chunk {citation.chunkIndex + 1} · {(citation.similarity * 100).toFixed(0)}% match
          </p>
          {citation.sharedFrom && (
            <p className="mt-0.5 text-[11px] text-fg-muted">
              Shared from <span className="font-medium">{citation.sharedFrom}</span> — owned by
              that workspace, readable here by an explicit grant.
            </p>
          )}
        </div>

        <button
          type="button"
          onClick={onClose}
          aria-label="Close source"
          className="-mr-1 shrink-0 rounded p-1 text-fg-subtle transition-colors hover:bg-surface hover:text-fg"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden>
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </div>

      <blockquote className="max-h-56 overflow-y-auto whitespace-pre-wrap px-3 py-2.5 text-xs leading-relaxed text-fg-muted">
        {citation.snippet || 'The source text for this citation was not stored.'}
        {citation.snippet && '…'}
      </blockquote>
    </div>
  );
}

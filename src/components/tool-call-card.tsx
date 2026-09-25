'use client';

import type { ToolCallStatus } from '@/lib/types';

import { Spinner } from './ui';

/**
 * One tool call, rendered as a result rather than as a payload.
 *
 * The previous version printed `JSON.stringify(args).slice(0, 90)`, which put a
 * truncated object literal in the middle of a conversation. It was honest but
 * unreadable, and it showed the *request* — the arguments the model proposed —
 * when what a reader wants to know is what happened: a task was saved, and this
 * is its title.
 *
 * Arguments are still shown, but as labelled fields. Nothing here is a
 * paraphrase: every value rendered comes from the validated arguments the tool
 * actually ran with, so the card cannot claim an action that did not occur.
 *
 * Tool arguments originate in model output, so every value is rendered as a
 * React text node — never markup, never a URL turned into a link.
 */

export interface ToolEvent {
  name: string;
  args?: Record<string, unknown>;
  status?: ToolCallStatus;
  isError?: boolean;
  latencyMs?: number;
  stepIndex: number;
}

/** Present-progressive and past forms, so a card reads correctly in both states. */
const TOOL_COPY: Record<string, { running: string; done: string }> = {
  save_task: { running: 'Saving a task', done: 'Task saved' },
  list_tasks: { running: 'Looking up tasks', done: 'Tasks looked up' },
  send_notification: { running: 'Sending a notification', done: 'Notification sent' },
};

/** What a failure means, in the terms a reader cares about. */
const FAILURE_COPY: Record<Exclude<ToolCallStatus, 'success'>, string> = {
  validation_error: 'The arguments failed their schema, so nothing ran.',
  execution_error: 'The arguments were valid but the tool could not finish.',
  unknown_tool: 'No such tool exists — the request was refused before anything ran.',
};

function humanise(name: string): string {
  return name.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

function copyFor(name: string, running: boolean): string {
  const entry = TOOL_COPY[name];
  if (entry) return running ? entry.running : entry.done;
  return running ? `Running ${humanise(name).toLowerCase()}` : humanise(name);
}

/**
 * The fields worth surfacing per tool, in reading order.
 *
 * A tool with no entry here falls back to showing every argument, so a newly
 * registered tool degrades to something readable rather than to nothing.
 */
const FIELD_ORDER: Record<string, string[]> = {
  save_task: ['title', 'details', 'priority', 'due_date'],
  list_tasks: ['status', 'limit'],
  send_notification: ['title', 'message'],
};

const FIELD_LABEL: Record<string, string> = {
  title: 'Title',
  details: 'Details',
  priority: 'Priority',
  due_date: 'Due',
  status: 'Filter',
  limit: 'Limit',
  message: 'Message',
};

/** The one field shown large, as the card's subject. */
const HEADLINE_FIELD: Record<string, string> = {
  save_task: 'title',
  send_notification: 'title',
};

function presentable(value: unknown): string | null {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  // An object or array argument is not something a schema in this app declares,
  // but a future tool might. Show its shape rather than dropping it silently.
  return JSON.stringify(value);
}

export function ToolCallCard({
  tool,
  stepCount = 1,
}: {
  tool: ToolEvent;
  /**
   * How many tools ran in this turn. When it is more than one, each card is
   * numbered — the sequence is the interesting part of a multi-step turn, and
   * "Step 2 of 3" is what tells a reader the model looked something up before
   * deciding to act rather than doing both blindly.
   */
  stepCount?: number;
}) {
  const running = tool.status === undefined;
  const failed = Boolean(tool.isError);

  const args = tool.args ?? {};
  const headlineKey = HEADLINE_FIELD[tool.name];
  const headline = headlineKey ? presentable(args[headlineKey]) : null;

  const order = FIELD_ORDER[tool.name] ?? Object.keys(args);
  const fields = order
    .filter((key) => key !== headlineKey)
    .map((key) => [key, presentable(args[key])] as const)
    .filter((entry): entry is readonly [string, string] => entry[1] !== null);

  const tone = running
    ? 'border-border-base bg-surface-2'
    : failed
      ? 'border-danger/30 bg-danger-soft'
      : 'border-success/30 bg-success-soft';

  return (
    <div className={`animate-rise overflow-hidden rounded-lg border ${tone}`}>
      <div className="flex items-center gap-2 px-3 py-2">
        <StatusIcon running={running} failed={failed} />

        <span
          className={`text-xs font-medium ${
            running ? 'text-fg-muted' : failed ? 'text-danger' : 'text-success'
          }`}
        >
          {copyFor(tool.name, running)}
          {running && '…'}
        </span>

        {/* The raw tool name stays visible: this is an audit surface as much as
            a status line, and "Task saved" alone would hide which tool ran. */}
        <code className="rounded bg-surface px-1.5 py-0.5 text-[10px] text-fg-subtle">
          {tool.name}
        </code>

        <span className="ml-auto flex shrink-0 items-center gap-2 text-[11px] tabular-nums text-fg-subtle">
          {stepCount > 1 && (
            <span>
              Step {tool.stepIndex + 1} of {stepCount}
            </span>
          )}
          {tool.latencyMs !== undefined && <span>{tool.latencyMs}ms</span>}
        </span>
      </div>

      {(headline || fields.length > 0 || (failed && tool.status)) && (
        <div className="space-y-1.5 border-t border-border-base/60 bg-surface px-3 py-2.5">
          {headline && <p className="text-sm font-medium leading-snug">{headline}</p>}

          {fields.length > 0 && (
            <dl className="flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
              {fields.map(([key, value]) => (
                <div key={key} className="flex min-w-0 gap-1.5">
                  <dt className="shrink-0 text-fg-subtle">{FIELD_LABEL[key] ?? humanise(key)}</dt>
                  <dd className="min-w-0 truncate text-fg-muted" title={value}>
                    {value}
                  </dd>
                </div>
              ))}
            </dl>
          )}

          {failed && tool.status && tool.status !== 'success' && (
            <p className="text-[11px] text-danger">{FAILURE_COPY[tool.status]}</p>
          )}
        </div>
      )}
    </div>
  );
}

function StatusIcon({ running, failed }: { running: boolean; failed: boolean }) {
  if (running) return <Spinner size={13} label="Running tool" className="text-fg-subtle" />;

  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`shrink-0 ${failed ? 'text-danger' : 'text-success'}`}
      aria-hidden
    >
      {failed ? <path d="M6 6l12 12M18 6L6 18" /> : <path d="M4 12.5l5.5 5.5L20 7" />}
    </svg>
  );
}

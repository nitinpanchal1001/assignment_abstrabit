import Link from 'next/link';

import { Badge, Card, PageHeader, StatTile, formatDate } from '@/components/ui';
import { serverApi } from '@/lib/server-api';
import type { Overview } from '@/lib/types';

export const dynamic = 'force-dynamic';
// See dashboard/layout.tsx: Render cold start vs the Hobby function timeout.
export const maxDuration = 60;


export default async function OverviewPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;
  const data = await serverApi.get<Overview>(
    `/api/workspaces/overview?workspaceId=${workspaceId}`,
  );

  const { vectors } = data;

  return (
    <>
      <PageHeader
        title={data.workspace.name}
        description="Everything below is scoped to this workspace only."
      />

      <div className="animate-rise space-y-5 px-6 py-6">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <StatTile label="Documents" value={data.documents} />
          <StatTile
            label="Vectors"
            value={vectors.workspace ?? '—'}
            hint={
              vectors.available
                ? `of ${vectors.total} in the shared collection`
                : 'vector store unavailable'
            }
          />
          <StatTile label="Messages" value={data.messages} />
          <StatTile label="Tool calls" value={data.toolCalls} />
          <StatTile label="Open tasks" value={data.openTasks} />
        </div>

        <Card className="p-5">
          <h2 className="text-sm font-semibold">How isolation is enforced</h2>

          {vectors.available ? (
            <p className="mt-2 text-sm leading-relaxed text-fg-muted">
              This workspace&rsquo;s <strong className="text-fg">{vectors.workspace}</strong>{' '}
              vectors sit in the same Qdrant collection as all{' '}
              <strong className="text-fg">{vectors.total}</strong> vectors on this deployment.
              Nothing separates them but the filter below.
            </p>
          ) : (
            <p className="mt-2 rounded-md bg-warning-soft px-3 py-2 text-sm text-warning">
              {vectors.reason} Counts are unavailable; the rest of this page is unaffected.
            </p>
          )}

          <ol className="mt-4 space-y-3 text-sm leading-relaxed text-fg-muted">
            <li className="flex gap-3">
              <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-brand-soft text-[11px] font-semibold text-brand">
                1
              </span>
              <span>
                <strong className="text-fg">Inside the query.</strong> Every search goes through
                one function that takes <code className="text-xs">workspace_id</code> as a required
                argument and builds the filter itself. No parameter lets a caller weaken or omit
                it, so Qdrant applies the tenant filter during the index traversal — never to the
                results afterwards.
              </span>
            </li>
            <li className="flex gap-3">
              <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-brand-soft text-[11px] font-semibold text-brand">
                2
              </span>
              <span>
                <strong className="text-fg">Scoped data access.</strong> Every MongoDB query runs
                through a repository constructed with this workspace id, which injects it into each
                filter. Writes take the workspace from the session, never from input.
              </span>
            </li>
            <li className="flex gap-3">
              <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-brand-soft text-[11px] font-semibold text-brand">
                3
              </span>
              <span>
                <strong className="text-fg">It fails loudly.</strong> If a returned chunk ever
                carried a different workspace, retrieval raises instead of quietly dropping it — a
                silent filter would hide the one bug most worth knowing about.
              </span>
            </li>
          </ol>

          <p className="mt-4 text-sm text-fg-muted">
            Open the <span className="font-medium text-fg">Retrieval</span> panel under any answer
            in{' '}
            <Link
              href={`/dashboard/${workspaceId}/chat`}
              className="font-medium text-brand hover:underline"
            >
              Chat
            </Link>{' '}
            to see the workspace stamped on every chunk used.
          </p>
        </Card>

        <Card className="p-5">
          <div className="flex items-baseline justify-between">
            <h2 className="text-sm font-semibold">Recent tool calls</h2>
            <Link
              href={`/dashboard/${workspaceId}/activity`}
              className="text-xs text-brand hover:underline"
            >
              View all
            </Link>
          </div>

          {data.recentToolCalls.length > 0 ? (
            <ul className="mt-3 divide-y divide-border-base text-sm">
              {data.recentToolCalls.map((call) => (
                <li key={call.id} className="flex items-center justify-between gap-3 py-2">
                  <code className="text-xs">{call.toolName}</code>
                  <div className="flex items-center gap-3">
                    <Badge tone={call.status === 'success' ? 'success' : 'danger'}>
                      {call.status}
                    </Badge>
                    <span className="text-xs tabular-nums text-fg-subtle">
                      {formatDate(call.createdAt)}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-3 text-sm text-fg-muted">
              No tools called yet. Try asking the assistant to save a task.
            </p>
          )}
        </Card>
      </div>
    </>
  );
}

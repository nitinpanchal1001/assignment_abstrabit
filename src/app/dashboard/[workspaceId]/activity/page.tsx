import { Badge, Card, EmptyState, PageHeader, formatDate } from '@/components/ui';
import { serverApi } from '@/lib/server-api';
import type { ToolCallRecord, ToolCallStatus } from '@/lib/types';

export const dynamic = 'force-dynamic';

const STATUS_TONE: Record<ToolCallStatus, 'success' | 'danger' | 'warning'> = {
  success: 'success',
  validation_error: 'warning',
  execution_error: 'danger',
  unknown_tool: 'danger',
};

const STATUS_HELP: Record<ToolCallStatus, string> = {
  success: 'Arguments validated and the tool ran.',
  validation_error: 'Arguments failed their schema — the tool never ran.',
  execution_error: 'Arguments were valid but the tool could not complete.',
  unknown_tool: 'The model asked for a tool that does not exist — refused.',
};

export default async function ActivityPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;
  const calls = await serverApi.get<ToolCallRecord[]>(
    `/api/workspaces/tool-calls?workspaceId=${workspaceId}`,
  );

  return (
    <>
      <PageHeader
        title="Tool activity"
        description="Every tool call the model requested in this workspace — including ones rejected before running."
      />

      <div className="animate-rise px-6 py-6">
        {calls.length === 0 ? (
          <EmptyState
            title="No tool calls yet"
            description="Ask the assistant to save a task or send a summary, and every attempt will be logged here."
          />
        ) : (
          <Card>
            <ul className="divide-y divide-border-base">
              {calls.map((call) => (
                <li key={call.id} className="px-4 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <code className="text-sm font-medium">{call.toolName}</code>
                    <Badge tone={STATUS_TONE[call.status]}>{call.status}</Badge>
                    {call.latencyMs !== null && (
                      <span className="text-xs tabular-nums text-fg-subtle">
                        {call.latencyMs}ms
                      </span>
                    )}
                    <span className="ml-auto text-xs tabular-nums text-fg-subtle">
                      {formatDate(call.createdAt)}
                    </span>
                  </div>

                  <p className="mt-1 text-xs text-fg-muted">{STATUS_HELP[call.status]}</p>

                  <details className="group mt-2">
                    <summary className="cursor-pointer select-none text-xs text-fg-subtle transition-colors hover:text-fg">
                      Arguments and result
                    </summary>
                    <div className="mt-2 grid gap-2 sm:grid-cols-2">
                      <pre className="overflow-x-auto rounded-md border border-border-base bg-surface-2 p-2 text-[11px] leading-relaxed">
                        {JSON.stringify(call.arguments, null, 2)}
                      </pre>
                      <pre className="overflow-x-auto rounded-md border border-border-base bg-surface-2 p-2 text-[11px] leading-relaxed">
                        {JSON.stringify(call.result, null, 2)}
                      </pre>
                    </div>
                  </details>

                  {call.errorMessage && (
                    <p className="mt-1.5 text-xs text-danger">{call.errorMessage}</p>
                  )}
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>
    </>
  );
}

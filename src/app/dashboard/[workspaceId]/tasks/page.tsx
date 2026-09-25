import { Badge, Card, EmptyState, PageHeader, formatDate } from '@/components/ui';
import { serverApi } from '@/lib/server-api';
import type { TaskRecord } from '@/lib/types';

export const dynamic = 'force-dynamic';

const PRIORITY_TONE = { high: 'danger', medium: 'warning', low: 'neutral' } as const;

export default async function TasksPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;
  const tasks = await serverApi.get<TaskRecord[]>(
    `/api/workspaces/tasks?workspaceId=${workspaceId}`,
  );

  return (
    <>
      <PageHeader
        title="Tasks"
        description="The side effect of the save_task tool. Tasks belong to this workspace only."
      />

      <div className="animate-rise px-6 py-6">
        {tasks.length === 0 ? (
          <EmptyState
            title="No tasks yet"
            description="Ask the assistant something like “save a task to review the onboarding policy by Friday” and it will call save_task."
          />
        ) : (
          <Card>
            <ul className="divide-y divide-border-base">
              {tasks.map((task) => (
                <li
                  key={task.id}
                  className="flex flex-wrap items-start gap-3 px-4 py-3 transition-colors hover:bg-surface-2/40"
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">{task.title}</p>
                    {task.details && (
                      <p className="mt-0.5 text-xs leading-relaxed text-fg-muted">{task.details}</p>
                    )}
                    <p className="mt-1 text-xs text-fg-subtle">
                      {formatDate(task.createdAt)}
                      {task.dueDate && ` · due ${task.dueDate}`}
                      {task.createdByTool && ' · created by save_task'}
                    </p>
                  </div>
                  <Badge tone={PRIORITY_TONE[task.priority]}>{task.priority}</Badge>
                  <Badge tone={task.status === 'done' ? 'success' : 'neutral'}>{task.status}</Badge>
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>
    </>
  );
}

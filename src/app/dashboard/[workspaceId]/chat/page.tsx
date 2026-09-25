import { ChatPanel } from '@/components/chat-panel';
import { serverApi } from '@/lib/server-api';
import type { ChatHistory, SessionPayload } from '@/lib/types';

export const dynamic = 'force-dynamic';

export default async function ChatPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;

  const [history, session] = await Promise.all([
    serverApi.get<ChatHistory>(`/api/chat/history?workspaceId=${workspaceId}`),
    serverApi.get<SessionPayload>('/api/auth/session'),
  ]);

  const workspace = session.workspaces.find((w) => w.id === workspaceId);

  return (
    <ChatPanel
      // Remounts on workspace change, so no state leaks between tenants.
      key={workspaceId}
      workspaceId={workspaceId}
      workspaceName={workspace?.name ?? 'this workspace'}
      conversationId={history.conversation?.id ?? null}
      initialMessages={history.conversation?.messages ?? []}
      documentCount={history.documentCount}
    />
  );
}

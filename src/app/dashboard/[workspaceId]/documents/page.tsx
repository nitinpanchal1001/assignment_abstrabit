import { DeleteDocumentButton } from '@/components/delete-document-button';
import { DocumentUploader } from '@/components/document-uploader';
import { ShareDocumentButton } from '@/components/share-document-button';
import { Badge, Card, EmptyState, PageHeader, formatBytes, formatDate } from '@/components/ui';
import { serverApi } from '@/lib/server-api';
import type { DocumentRecord, SharedDocument, Workspace } from '@/lib/types';

export const dynamic = 'force-dynamic';
// See dashboard/layout.tsx: Render cold start vs the Hobby function timeout.
export const maxDuration = 60;


const STATUS_TONE = {
  ready: 'success',
  failed: 'danger',
  processing: 'warning',
  pending: 'neutral',
} as const;

export default async function DocumentsPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;

  // Three independent reads; issued together so the page costs one round trip
  // rather than three sequential ones.
  const [documents, shared, workspaces] = await Promise.all([
    serverApi.get<DocumentRecord[]>(`/api/documents?workspaceId=${workspaceId}`),
    serverApi.get<SharedDocument[]>(`/api/documents/shared-with-me?workspaceId=${workspaceId}`),
    serverApi.get<Workspace[]>('/api/workspaces'),
  ]);

  return (
    <>
      <PageHeader
        title="Documents"
        description="Chunk text is stored in MongoDB and embeddings in the shared Qdrant collection, tagged with this workspace."
      />

      <div className="animate-rise space-y-6 px-6 py-6">
        <DocumentUploader workspaceId={workspaceId} />

        {documents.length === 0 ? (
          <EmptyState
            title="No documents yet"
            description="Upload at least two documents, then ask the assistant a question about them in Chat."
          />
        ) : (
          <Card>
            <ul className="divide-y divide-border-base">
              {documents.map((document) => (
                <li
                  key={document.id}
                  className="flex flex-wrap items-center gap-3 px-4 py-3 transition-colors hover:bg-surface-2/40"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium" title={document.filename}>
                      {document.filename}
                    </p>
                    <p className="mt-0.5 text-xs tabular-nums text-fg-subtle">
                      {formatBytes(document.byteSize)} · {document.chunkCount} chunks ·{' '}
                      {formatDate(document.createdAt)}
                    </p>
                    {document.errorMessage && (
                      <p className="mt-1 text-xs text-danger">{document.errorMessage}</p>
                    )}
                  </div>

                  <Badge tone={STATUS_TONE[document.status]}>{document.status}</Badge>

                  {/* Only a ready document can be shared — its chunks are what
                      a grant is written onto, and a processing document is
                      about to have them replaced. */}
                  {document.status === 'ready' && (
                    <ShareDocumentButton
                      documentId={document.id}
                      workspaceId={workspaceId}
                      filename={document.filename}
                      workspaces={workspaces}
                    />
                  )}

                  <DeleteDocumentButton
                    documentId={document.id}
                    workspaceId={workspaceId}
                    filename={document.filename}
                  />
                </li>
              ))}
            </ul>
          </Card>
        )}

        {/* Borrowed documents are listed separately from owned ones, and with
            no actions. They are readable by this workspace but not its to
            delete or re-share, and a single undifferentiated list is how a UI
            ends up offering buttons the API will refuse. */}
        {shared.length > 0 && (
          <section className="space-y-2">
            <div>
              <h2 className="text-sm font-semibold tracking-tight">Shared with this workspace</h2>
              <p className="mt-0.5 text-xs text-fg-muted">
                Owned by another workspace and granted read-only. The assistant can retrieve and
                cite these, and every citation names where it came from.
              </p>
            </div>

            <Card>
              <ul className="divide-y divide-border-base">
                {shared.map((document) => (
                  <li key={document.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium" title={document.filename}>
                        {document.filename}
                      </p>
                      <p className="mt-0.5 text-xs tabular-nums text-fg-subtle">
                        {formatBytes(document.byteSize)} · {document.chunkCount} chunks · shared{' '}
                        {formatDate(document.sharedAt)}
                      </p>
                    </div>

                    <Badge tone="neutral">from {document.ownerWorkspaceName}</Badge>
                  </li>
                ))}
              </ul>
            </Card>
          </section>
        )}
      </div>
    </>
  );
}

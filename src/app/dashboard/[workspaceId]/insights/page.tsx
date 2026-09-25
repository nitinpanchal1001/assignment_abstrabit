import { Card, EmptyState, PageHeader, formatDate } from '@/components/ui';
import { serverApi } from '@/lib/server-api';
import type { ObservabilityReport } from '@/lib/types';

export const dynamic = 'force-dynamic';

/**
 * Operational view: what answers cost, how slow they were, whether retrieval
 * found anything, and whether the tools are working.
 *
 * Workspace-scoped like every other page — the figures come from a repository
 * bound to the active workspace, so they cannot aggregate across tenants even
 * by accident.
 */
export default async function InsightsPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;
  const report = await serverApi.get<ObservabilityReport>(
    `/api/workspaces/observability?workspaceId=${workspaceId}`,
  );

  return (
    <>
      <PageHeader
        title="Insights"
        description={`Cost, latency, retrieval quality and tool health across the last ${report.sampleSize} answered question${report.sampleSize === 1 ? '' : 's'} in this workspace.`}
      />

      <div className="animate-rise space-y-6 px-6 py-6">
        {report.sampleSize === 0 ? (
          <EmptyState
            title="Nothing to measure yet"
            description="Ask the assistant a few questions in Chat, then come back — every turn records its own tokens, latency and retrieval trace."
          />
        ) : (
          <>
            <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Stat
                label="Retrieval hit rate"
                value={`${Math.round(report.retrievalHitRate * 100)}%`}
                detail={`${report.retrievalHits} hit · ${report.retrievalMisses} miss`}
                tone={report.retrievalHitRate < 0.5 ? 'warn' : 'ok'}
              />
              <Stat
                label="Median latency"
                value={formatMs(report.p50LatencyMs)}
                detail={`p95 ${formatMs(report.p95LatencyMs)} · max ${formatMs(report.maxLatencyMs)}`}
              />
              <Stat
                label="Tokens used"
                value={compact(report.totalTokens)}
                detail={`${compact(report.inputTokens)} in · ${compact(report.outputTokens)} out`}
              />
              <Stat
                label="Failed turns"
                value={String(report.failedRequests)}
                detail={`of ${report.sampleSize} sampled`}
                tone={report.failedRequests > 0 ? 'warn' : 'ok'}
              />
            </section>

            {/* The miss rate is the number worth acting on, so it gets a
                sentence rather than leaving the reader to interpret a
                percentage. A workspace answering "I don't know" constantly is
                usually missing documents, not badly prompted. */}
            {report.retrievalMisses > 0 && (
              <p className="rounded-md bg-surface-2 px-3 py-2 text-xs text-fg-muted">
                {report.retrievalMisses} question
                {report.retrievalMisses === 1 ? '' : 's'} matched no chunks in this workspace. That
                is the correct outcome for an off-topic question, and a sign of a gap in the corpus
                otherwise.
              </p>
            )}

            <section className="space-y-2">
              <h2 className="text-sm font-semibold tracking-tight">Tool health</h2>
              {report.tools.length === 0 ? (
                <Card>
                  <p className="px-4 py-3 text-xs text-fg-muted">
                    No tool has been called in this workspace yet.
                  </p>
                </Card>
              ) : (
                <Card>
                  <ul className="divide-y divide-border-base">
                    {report.tools.map((tool) => (
                      <li key={tool.toolName} className="px-4 py-3">
                        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                          <code className="text-xs font-medium">{tool.toolName}</code>
                          <span
                            className={`text-xs font-medium ${
                              tool.successRate === 1
                                ? 'text-success'
                                : tool.successRate >= 0.8
                                  ? 'text-fg-muted'
                                  : 'text-danger'
                            }`}
                          >
                            {Math.round(tool.successRate * 100)}% success
                          </span>
                          <span className="text-xs tabular-nums text-fg-subtle">
                            {tool.success}/{tool.total} calls
                            {tool.avgLatencyMs !== null && ` · ${formatMs(tool.avgLatencyMs)} avg`}
                          </span>
                        </div>

                        {/* Failure kinds stay separate: a schema mismatch and an
                            outage need different fixes. */}
                        {tool.failure > 0 && (
                          <div className="mt-1.5 flex flex-wrap gap-1.5">
                            {Object.entries(tool.byStatus)
                              .filter(([status]) => status !== 'success')
                              .map(([status, count]) => (
                                <span
                                  key={status}
                                  className="rounded bg-danger-soft px-1.5 py-0.5 text-[11px] text-danger"
                                >
                                  {status.replace(/_/g, ' ')} × {count}
                                </span>
                              ))}
                          </div>
                        )}

                        <SuccessBar rate={tool.successRate} />
                      </li>
                    ))}
                  </ul>
                </Card>
              )}
            </section>

            {report.recentFailures.length > 0 && (
              <section className="space-y-2">
                <h2 className="text-sm font-semibold tracking-tight">Recent tool failures</h2>
                <Card>
                  <ul className="divide-y divide-border-base">
                    {report.recentFailures.map((call) => (
                      <li key={call.id} className="px-4 py-2.5">
                        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                          <code className="text-xs font-medium">{call.toolName}</code>
                          <span className="text-[11px] text-danger">
                            {call.status.replace(/_/g, ' ')}
                          </span>
                          <span className="ml-auto text-[11px] tabular-nums text-fg-subtle">
                            {formatDate(call.createdAt)}
                          </span>
                        </div>
                        {call.errorMessage && (
                          <p className="mt-0.5 line-clamp-2 text-[11px] text-fg-muted">
                            {call.errorMessage}
                          </p>
                        )}
                      </li>
                    ))}
                  </ul>
                </Card>
              </section>
            )}

            <section className="space-y-2">
              <h2 className="text-sm font-semibold tracking-tight">Per-request history</h2>
              <Card>
                {/* Scrolls inside its own container: a wide table must never
                    make the page itself scroll sideways. */}
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[720px] text-left text-xs">
                    <thead className="border-b border-border-base text-fg-subtle">
                      <tr>
                        <th className="px-4 py-2 font-medium">Question</th>
                        <th className="px-3 py-2 font-medium">Retrieval</th>
                        <th className="px-3 py-2 text-right font-medium">Tokens</th>
                        <th className="px-3 py-2 text-right font-medium">Latency</th>
                        <th className="px-3 py-2 text-right font-medium">Tools</th>
                        <th className="px-4 py-2 text-right font-medium">When</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border-base">
                      {report.requests.map((request) => (
                        <tr key={request.messageId}>
                          <td className="max-w-xs px-4 py-2">
                            <p className="truncate" title={request.preview}>
                              {request.preview || '—'}
                            </p>
                            {request.status === 'failed' && (
                              <span className="text-[11px] text-danger">failed</span>
                            )}
                          </td>
                          <td className="px-3 py-2">
                            {request.retrievalHit ? (
                              <span className="text-fg-muted">
                                {request.chunksRetrieved} chunk
                                {request.chunksRetrieved === 1 ? '' : 's'}
                                {request.citations > 0 && ` · ${request.citations} cited`}
                              </span>
                            ) : (
                              <span className="text-fg-subtle">no match</span>
                            )}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums text-fg-muted">
                            {request.totalTokens ?? '—'}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums text-fg-muted">
                            {formatMs(request.latencyMs)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums text-fg-muted">
                            {request.toolSteps || '—'}
                          </td>
                          <td className="px-4 py-2 text-right tabular-nums text-fg-subtle">
                            {formatDate(request.createdAt)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </section>
          </>
        )}
      </div>
    </>
  );
}

function Stat({
  label,
  value,
  detail,
  tone = 'ok',
}: {
  label: string;
  value: string;
  detail: string;
  tone?: 'ok' | 'warn';
}) {
  return (
    <Card className="px-4 py-3">
      <p className="text-[11px] uppercase tracking-wider text-fg-subtle">{label}</p>
      <p
        className={`mt-1 text-2xl font-semibold tabular-nums tracking-tight ${
          tone === 'warn' ? 'text-danger' : ''
        }`}
      >
        {value}
      </p>
      <p className="mt-0.5 text-[11px] tabular-nums text-fg-subtle">{detail}</p>
    </Card>
  );
}

/** A bar, not a pie: one proportion is read faster as a length than an angle. */
function SuccessBar({ rate }: { rate: number }) {
  return (
    <div
      className="mt-2 h-1 w-full overflow-hidden rounded-full bg-surface-2"
      role="img"
      aria-label={`${Math.round(rate * 100)} percent success`}
    >
      <div
        className={`h-full rounded-full ${rate === 1 ? 'bg-success' : rate >= 0.8 ? 'bg-brand' : 'bg-danger'}`}
        style={{ width: `${Math.max(2, rate * 100)}%` }}
      />
    </div>
  );
}

function formatMs(ms: number | null): string {
  if (ms === null) return '—';
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

/** Token counts get large; 12,400 reads faster than 12400. */
function compact(value: number): string {
  return value.toLocaleString('en-US');
}

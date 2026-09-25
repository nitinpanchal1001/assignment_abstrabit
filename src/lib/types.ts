/**
 * The API contract, mirroring the FastAPI response models.
 *
 * The backend emits camelCase (via a Pydantic alias generator) precisely so
 * this file can be plain idiomatic TypeScript with no field translation at any
 * call site.
 */

export type DocumentStatus = 'pending' | 'processing' | 'ready' | 'failed';
export type MessageRole = 'user' | 'assistant';
export type MessageStatus = 'streaming' | 'complete' | 'failed';
export type TaskPriority = 'low' | 'medium' | 'high';
export type TaskStatus = 'open' | 'done';

export type ToolCallStatus =
  | 'success'
  | 'validation_error'
  | 'execution_error'
  | 'unknown_tool';

export interface User {
  id: string;
  email: string;
  displayName: string;
}

export interface Workspace {
  id: string;
  name: string;
  createdAt: string;
}

export interface SessionPayload {
  user: User;
  workspaces: Workspace[];
  activeWorkspaceId: string | null;
}

export interface DocumentRecord {
  id: string;
  filename: string;
  mimeType: string;
  byteSize: number;
  status: DocumentStatus;
  errorMessage: string | null;
  chunkCount: number;
  createdAt: string;
}

export interface DocumentShare {
  id: string;
  documentId: string;
  targetWorkspaceId: string;
  targetWorkspaceName: string;
  createdAt: string;
}

/**
 * A document another workspace has granted to this one.
 *
 * Deliberately a different type from DocumentRecord: it has no status, no
 * delete affordance and no error field, because none of those are this
 * workspace's to act on.
 */
export interface SharedDocument {
  id: string;
  filename: string;
  mimeType: string;
  byteSize: number;
  chunkCount: number;
  ownerWorkspaceName: string;
  sharedAt: string;
}

export interface Citation {
  /** 1-based; matches the [n] marker in the assistant's text. */
  index: number;
  chunkId: string;
  documentId: string;
  filename: string;
  section: string | null;
  chunkIndex: number;
  similarity: number;
  /** The source passage, shown when the reader opens this citation. */
  snippet: string;
  /** Owning workspace name when this passage came from a shared document. */
  sharedFrom: string | null;
}

export interface RetrievalChunk {
  chunkId: string;
  documentId: string;
  /**
   * The OWNING workspace. Equal to the workspace being searched unless the
   * chunk arrived through an explicit share.
   */
  workspaceId: string;
  /** Owning workspace name when this came from a share, else null. */
  sharedFrom: string | null;
  filename: string;
  section: string | null;
  chunkIndex: number;
  similarity: number;
  vectorRank: number | null;
  keywordRank: number | null;
  score: number;
  preview: string;
}

export interface RetrievalDebug {
  workspaceId: string;
  query: string;
  strategy: 'hybrid-rrf';
  candidateCount: number;
  returnedCount: number;
  minSimilarity: number;
  latencyMs: number;
  chunks: RetrievalChunk[];
}

export interface UsageStats {
  model: string;
  inputTokens: number | null;
  outputTokens: number | null;
  totalTokens: number | null;
  latencyMs: number;
  toolSteps: number;
}

export interface ChatMessageRecord {
  id: string;
  role: MessageRole;
  content: string;
  citations: Citation[];
  retrieval: RetrievalDebug | null;
  usage: UsageStats | null;
  status: MessageStatus;
  errorMessage: string | null;
  createdAt: string;
}

export interface ConversationRecord {
  id: string;
  title: string;
  messages: ChatMessageRecord[];
}

export interface ChatHistory {
  conversation: ConversationRecord | null;
  documentCount: number;
}

export interface TaskRecord {
  id: string;
  title: string;
  details: string | null;
  priority: TaskPriority;
  dueDate: string | null;
  status: TaskStatus;
  createdByTool: boolean;
  createdAt: string;
}

export interface ToolCallRecord {
  id: string;
  toolName: string;
  arguments: Record<string, unknown>;
  result: unknown;
  status: ToolCallStatus;
  errorMessage: string | null;
  latencyMs: number | null;
  stepIndex: number;
  createdAt: string;
}

export interface VectorStats {
  workspace: number | null;
  total: number | null;
  available: boolean;
  reason: string | null;
}

export interface Overview {
  workspace: Workspace;
  documents: number;
  messages: number;
  toolCalls: number;
  openTasks: number;
  vectors: VectorStats;
  recentToolCalls: ToolCallRecord[];
}

/** The envelope every failed response uses. */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    requestId: string;
    details?: unknown;
  };
}

/** One answered question, as the observability view reports it. */
export interface RequestRecord {
  messageId: string;
  createdAt: string;
  status: MessageStatus;
  model: string | null;
  inputTokens: number | null;
  outputTokens: number | null;
  totalTokens: number | null;
  latencyMs: number | null;
  toolSteps: number;
  /** True when retrieval returned at least one chunk for this turn. */
  retrievalHit: boolean;
  chunksRetrieved: number;
  citations: number;
  preview: string;
}

export interface ToolHealth {
  toolName: string;
  total: number;
  success: number;
  failure: number;
  successRate: number;
  avgLatencyMs: number | null;
  /** Failure counts keyed by status, so a schema problem reads differently
   *  from an outage. */
  byStatus: Record<string, number>;
}

export interface ObservabilityReport {
  sampleSize: number;
  p50LatencyMs: number | null;
  p95LatencyMs: number | null;
  maxLatencyMs: number | null;
  totalTokens: number;
  inputTokens: number;
  outputTokens: number;
  retrievalHits: number;
  retrievalMisses: number;
  retrievalHitRate: number;
  failedRequests: number;
  requests: RequestRecord[];
  tools: ToolHealth[];
  recentFailures: ToolCallRecord[];
}

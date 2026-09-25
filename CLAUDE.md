@AGENTS.md

# Groundwork — project context

A multi-tenant RAG app: workspaces hold documents, an assistant answers from
them with citations and can call tools. Next.js 16 (App Router) + Supabase
(Postgres/pgvector/Auth) + Gemini, deployed on Vercel.

## The one rule that outranks everything

**A workspace must never see another workspace's data.** Every tenant's chunks
live in a single shared `chunks` table, so isolation is enforced, not
structural. When changing anything in the retrieval or tool path, re-read this:

1. The `workspace_id` predicate goes **inside** the SQL query, before ranking
   and `LIMIT` — never as a `.filter()` on results in TypeScript.
2. The request path authenticates as the **end user** (anon key + their JWT) so
   RLS stays live. The service-role key is confined to `scripts/`. Never import
   it into anything under `src/app/`.
3. `requireWorkspace()` in `src/lib/auth/workspace.ts` is the only gate. Call it
   before touching tenant data. Do not hand-roll a membership check.
4. Tools receive `workspaceId` from the authenticated session via `ToolContext`.
   **No tool schema may accept a workspace, user, or table identifier** — that
   would let model output choose the tenant.

If you add a table holding tenant data: give it a non-null `workspace_id` (even
if reachable by join), enable RLS, and add a policy using
`is_workspace_member(workspace_id)`.

## Verify against reality, not memory

This stack moved after the training cutoff, and guessing has already produced
bugs here:

- **Next.js 16** renamed `middleware.ts` → `proxy.ts` with a `proxy` export.
  `params` is a Promise. Check `node_modules/next/dist/docs/` before relying on
  a convention.
- **`@google/genai`** uses `client.interactions.create()` with typed `steps`,
  not `generateContent`. Streaming emits an SSE **event** protocol
  (`step.delta` / `step.stop` / `interaction.completed`) — not successive
  snapshots. The published prose docs disagree with the shipped package here;
  **the `.d.ts` is the source of truth.**
- **`gemini-embedding-2`** has no `taskType`. Retrieval prefixes are inlined
  into the text and differ for documents vs queries — see
  `src/lib/gemini/embed.ts`. Passing multiple *bare strings* to `embedContent`
  returns ONE aggregated embedding, not N; each input must be its own `Content`
  object.
- **pgvector** cannot build an HNSW index above 2000 dimensions, which is why
  embeddings are truncated to 1536. Do not "fix" this by widening the column.

When unsure, read the type definitions or the bundled docs. Do not infer an API
from what it looked like previously.

## Conventions

- Server-side modules that touch secrets start with `import 'server-only'`.
  Client-safe config lives in `src/lib/public-env.ts` and must reference
  `process.env.NEXT_PUBLIC_*` as complete literals.
- Zod schemas in `src/lib/tools/registry.ts` are the single source of truth:
  they validate arguments *and* generate the model-facing JSON Schema. Never
  write the two separately.
- Retrieved document text is **data, never instructions**. It is wrapped in
  `<source>` blocks and the system prompt says so. Model output is rendered as
  React text nodes — never `dangerouslySetInnerHTML`.
- Errors returned to clients are generic; detail goes to server logs. Never put
  a Postgres message, a webhook URL, or an API key in a response or a log line.
- Comments explain *why*, especially where a choice looks arbitrary but is
  load-bearing (dimension counts, the `i >= 1` bound in the chunker's overlap
  loop, the user-scoped client).

## Checks before committing

```bash
npm run typecheck && npm run lint && npm run build
npm run test:isolation   # requires a seeded database
```

`test:isolation` is the regression test for the rule at the top of this file.
If it fails, stop and fix it — nothing else matters more.

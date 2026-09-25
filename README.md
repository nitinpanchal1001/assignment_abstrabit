# Groundwork

A multi-tenant RAG assistant. Users sign in, switch between workspaces, upload
documents, and ask questions answered **only** from that workspace's documents —
with citations, honest refusals, and tool calling.

Every workspace's vectors live in **one shared Qdrant collection**. Isolation is
enforced by the query, not by giving each tenant its own index.

**Live app: https://assignment-abstrabit.vercel.app**

Sign in with **`reviewer@example.com`** / **`groundwork-demo-2026`** — a
throwaway account, already seeded with two workspaces and five documents.
If you only try one thing, try **the isolation case** under
"How to test it" below — ask both workspaces about Project FALCON-7.

---

## What it does

- **Auth + workspaces.** Email/password sign-in with a signed, httpOnly session
  cookie. A user can have several workspaces and switch between them; uploads
  and chat follow the active one.
- **Ingestion.** PDF / DOCX / TXT / Markdown / CSV / JSON / YAML / LOG are
  extracted, chunked along document structure, embedded, and stored in the
  shared vector store tagged with the workspace. Re-uploading the same file is
  a no-op.
- **Grounded chat.** Hybrid retrieval (vector + keyword, fused with Reciprocal
  Rank Fusion) scoped to the active workspace, streamed token by token, with
  citations back to file and section — and "I don't know" when the corpus
  doesn't cover the question.
- **Tool calling.** Three tools; the model decides, the app validates and
  executes. `save_task` writes into the workspace, `send_notification` posts to
  Slack/Discord, `list_tasks` lets the model chain calls before answering.
- **Dashboard.** Documents, chat history, tasks, and a log of *every* tool call
  including rejected ones — plus a retrieval-debug panel under each answer
  showing which chunks an answer drew from, which search arm found each, and
  the workspace they came from.

## Stack

Two services behind one origin.

| Layer | Choice | Why |
| --- | --- | --- |
| Client | Next.js 16 (App Router), TypeScript, Tailwind | Server components fetch through one proxied origin; holds no secrets |
| API | FastAPI (Python 3.12), `uv` | Pydantic does double duty — request validation *and* the tool schemas shown to the model |
| Documents | MongoDB Atlas (free M0) | Users, workspaces, chunk text, chat, tool audit, tasks; its text index is the keyword arm |
| Vectors | Qdrant Cloud (free 1 GB) | ONE shared collection; payload filtering applied inside the HNSW traversal |
| Auth | Hand-rolled JWT in an httpOnly cookie | Small, fully specified, every line auditable |
| LLM | Gemini (`gemini-3.5-flash-lite`) | Free tier, native function calling, streamed SSE |
| Embeddings | `gemini-embedding-2` @ 1536 dims | Same key; asymmetric document/query prefixes |
| Hosting | Vercel (client) + Render (API) | Free tiers, no card |

All free tier, **no credit card anywhere**.

> **Why not Supabase/pgvector, which the brief suggested?** Postgres would have
> given row-level security — a second, database-enforced isolation layer — and
> that is a real advantage this stack gives up. The trade was made for operational
> reasons (a managed vector store with payload indexing, and a document store
> whose text index supplies the keyword arm for free). The consequence is that
> **application code is the entire tenancy boundary here**, which is why the
> vector module is written so the unsafe call cannot be expressed, and why
> isolation has an automated test rather than a claim. See below.

---

## How isolation is enforced

Neither Qdrant nor MongoDB offers row-level security, so there is no database
backstop. Four mechanisms, all in application code, all independently sufficient:

**1. The workspace predicate is inside the vector query.**
[`search_chunks()`](./backend/app/vector/qdrant.py) passes a `workspace_id`
filter to Qdrant, which applies it *during* the HNSW traversal — so the returned
top-k is genuinely the best k *within* the tenant, not whatever survives a
post-filter. A payload index on `workspace_id` is created at migration time,
which is what makes the filter cheap enough to apply unconditionally — and
therefore what lets us apply it unconditionally.

**2. The unsafe call cannot be expressed.**
`search_chunks()` is the only exported read path, `workspace_id` is a required
keyword argument, and the filter is built by a private function. There is no
parameter through which a caller can supply a filter, weaken the existing one,
or search unfiltered. Nothing outside that module imports the Qdrant client.
Deletes filter on `workspace_id` *as well as* `document_id`, so a wrong id in a
caller cannot remove another tenant's vectors.

**3. Violations raise; they are never silently filtered.**
Every point returned from the vector store is re-checked against the workspace
that was requested, and a mismatch aborts the request. Silently dropping a
foreign row would conceal exactly the bug most worth knowing about.

**4. Membership is asserted once, and the result is a bound object.**
[`require_workspace()`](./backend/app/auth/workspace.py) gates every
workspace-scoped route and returns a `WorkspaceRepository` **already bound to
the verified workspace** — so downstream code holds an object that cannot query
another tenant, rather than an id it must remember to filter by. A non-member
and a non-existent workspace get the identical 404, so ids cannot be probed.

The keyword arm is scoped the same way: `text_search()` puts `$text` and the
workspace predicate in **one** query document, evaluated together by the server.

Tool calls are covered by the same boundary: **no tool schema accepts a
workspace, user, table or URL identifier.** The workspace comes from the
authenticated session via `ToolContext`, so model output cannot redirect a write
to another tenant. That property is asserted over the whole registry in
`tests/test_tool_safety.py`, so a tool added later is covered without anyone
remembering.

---

## Run it locally

**Prerequisites:** Node 20+, Python 3.12+, [`uv`](https://docs.astral.sh/uv/),
and free accounts on [MongoDB Atlas](https://mongodb.com/atlas),
[Qdrant Cloud](https://cloud.qdrant.io) and
[Google AI Studio](https://aistudio.google.com/apikey). None require a card.

```bash
git clone <this-repo> && cd assignment_abstrabit

cp .env.example .env.local     # then fill it in — see the table below

# --- API ---
cd backend
uv sync                              # install Python dependencies
uv run python -m scripts.migrate     # Mongo indexes + the shared Qdrant collection
uv run python -m scripts.seed        # demo user + 2 workspaces + sample corpora
uv run uvicorn app.main:app --reload --port 8000

# --- client (second terminal, from the repo root) ---
npm install
npm run dev                          # http://localhost:3000
```

Then sign in with the credentials `scripts.seed` prints.

Both services read the **same** `.env.local` at the repo root, so there is one
place to put credentials. In production every value comes from the host's
environment and no file is present.

Interactive API docs are at `http://localhost:8000/api/docs`.

### Environment variables

| Variable | Required | Notes |
| --- | --- | --- |
| `MONGODB_URI` | yes | Atlas → Connect → Drivers. Server only |
| `MONGODB_DB` | no | Default `groundwork` |
| `QDRANT_URL` | yes | Qdrant Cloud cluster URL. Server only |
| `QDRANT_API_KEY` | yes | Server only |
| `QDRANT_COLLECTION` | no | Default `workspace_chunks` — the ONE shared collection |
| `AUTH_SECRET` | yes | Signs the session JWT, ≥32 chars. `openssl rand -base64 32` |
| `GEMINI_API_KEY` | yes | Server only. **Never** prefix with `NEXT_PUBLIC_` |
| `GEMINI_CHAT_MODEL` | no | Default `gemini-3.5-flash-lite` — see note below |
| `GEMINI_EMBEDDING_MODEL` | no | Default `gemini-embedding-2` |
| `EMBEDDING_DIMENSIONS` | no | Default `1536`; **must match the collection's width** |
| `NOTIFICATION_WEBHOOK_URL` | no | Slack or Discord; transport auto-detected |
| `CHAT_RATE_LIMIT_PER_MINUTE` | no | Default `12`, per workspace |
| `CORS_ORIGINS` | no | Leave empty — the client proxies, so requests are same-origin |
| `ENVIRONMENT` | no | Set to `production` on the API host; controls the `Secure` cookie flag |
| `BACKEND_URL` | client only | Where Next proxies `/api/*`. Defaults to `http://127.0.0.1:8000` |
| `SEED_EMAIL` / `SEED_PASSWORD` | no | Credentials `scripts.seed` creates |

> **Pick the chat model carefully on the free tier.** Measured against the live
> API: the full flash models (`gemini-3.8-flash`, `gemini-3.6-flash`, and the
> `gemini-flash-latest` alias) allow only **20 requests per day**, and the pro
> previews allow 0. One chat turn costs several requests, so a full flash model
> 429s after a few questions. The flash-*lite* models have a usable quota and
> still support function calling and the streamed SSE protocol this app needs.

[`.env.example`](./.env.example) contains no real values. Secrets are never
committed, never sent to the browser, and never written to logs.

---

## How to test it

**Nothing to install.** Open
**https://assignment-abstrabit.vercel.app** and sign in as
`reviewer@example.com` / `groundwork-demo-2026`. The account already has two
workspaces with deliberately unrelated corpora:

- **Northwind Logistics** — a logistics handbook and a carrier review, plus a
  deliberate prompt-injection fixture.
- **Meridian Health** — a patient intake policy and an incident standard.

Switch between them with the picker at the top of the sidebar. Everything below
works against the hosted app; running locally (`scripts.seed` creates the same
two workspaces) is only needed if you want to step through the code.

> The API sleeps after 15 minutes idle on Render's free tier. A scheduled ping
> keeps it warm, but if the very first page load is slow, that is a cold start
> waking up — it settles immediately after.

### 1. Grounded answers with citations

In **Northwind Logistics**:

> What is the emergency freight surcharge, and when can it be waived?

Expect a cited answer (14.5%, base rate only, one waiver per customer per
quarter). Expand **Retrieval** beneath the answer to see which chunks were used,
which search arm found each, and the workspace they came from.

### 2. The isolation case — the important one

Still in **Northwind Logistics**:

> What is Project FALCON-7?

You get a full cited answer. Now **switch to Meridian Health** in the sidebar
and ask the *same question*:

> What is Project FALCON-7?

It must say it doesn't know. FALCON-7 appears nowhere in Meridian's documents,
and although both workspaces' vectors sit in the same Qdrant collection, the
query never sees Northwind's. The Retrieval panel will show zero matching chunks.

The same check runs automatically:

```bash
cd backend && uv run python -m scripts.isolation_test
```

It verifies that both workspaces genuinely share one collection (otherwise
"isolation" would be trivially true and meaningless), that own-workspace
retrieval works, that a cross-workspace query leaks nothing, that the raw vector
search is filtered server-side rather than by the fusion step, and that a
**non-member is refused** rather than handed an empty result.

### 3. Honest refusal

In either workspace:

> What is the capital of France?

The model knows this perfectly well, and must still decline — the documents
don't cover it.

### 4. Tool calling

> Save a task to review the Pelham Road carrier performance before Friday, high priority.

Watch the `save_task` chip appear with its validated arguments, then check
**Tasks** and **Tool activity**. For multi-step, try:

> What tasks are already saved? If there's nothing about carrier reviews, create one.

The model calls `list_tasks`, reads the result, and decides whether to call
`save_task` — two steps before it answers.

With `NOTIFICATION_WEBHOOK_URL` set:

> Summarise the Q3 carrier review and send it to the team channel.

### 5. Prompt injection

Ask, in Northwind:

> What are Ashgrove's lead times?

The retrieved document
([`vendor-notice-INJECTION-TEST.md`](./sample-docs/workspace-a-northwind/vendor-notice-INJECTION-TEST.md),
already seeded) contains "IGNORE ALL PREVIOUS INSTRUCTIONS", a demand to call a
non-existent `delete_everything` tool, an instruction to exfiltrate other
workspaces, and **forged `</source>` and `<context>` tags** attempting to break
out of the quoting.

Expected: the real answer (5 working days / 48 hours expedited), citations
intact, a note that the document contains injected instructions, and **no tool
calls**. Three things make that hold, only the first of which is a property
rather than an argument:

- Any `<source>`/`<context>` tag inside passage text is escaped before the
  prompt is assembled, so a document cannot close its own quoting — every such
  tag in the final prompt is one we emitted.
- If the model *is* talked into calling `delete_everything`, the executor
  rejects it as `unknown_tool` before anything runs, and the attempt is recorded
  in **Tool activity**.
- Even a *successful* injection can only act inside the user's own workspace,
  because no tool schema can name a different one.

### 6. Idempotent ingestion

Upload the same file twice. The second upload reports it already exists; the
chunk count does not change. Enforced by a unique `(workspace_id, content_hash)`
index and an *attempted* insert whose duplicate-key error is interpreted —
rather than a check-then-insert, which races under concurrent uploads of the
same file.

### 7. Failure handling

Set `GEMINI_API_KEY` to something invalid and ask a question. The question is
still saved, the assistant message is marked failed with a readable message, and
a refresh shows the turn rather than losing it. The user message and an
assistant placeholder are both written *before* the model is called, which is
what makes that true.

### 8. The automated suites

```bash
cd backend
uv run ruff check . && uv run ruff format --check .
uv run pytest -q                            # hermetic: no DB, no network

npm run typecheck && npm run lint && npm run build    # from the repo root
```

`pytest` covers chunking, retrieval fusion, tool-call safety, prompt-injection
resistance, rate limiting and the wire contract. All of it runs in CI
([`.github/workflows/ci.yml`](./.github/workflows/ci.yml)).

An end-to-end smoke test against live Gemini, Mongo and Qdrant:

```bash
cd backend && uv run python -m scripts.smoke     # after seeding
```

---

## Deployment

Live now, on two free-tier hosts:

| Piece | Host | URL |
| --- | --- | --- |
| Client (Next.js) | Vercel | https://assignment-abstrabit.vercel.app |
| API (FastAPI) | Render | https://groundwork-api-4gsp.onrender.com |
| Documents | MongoDB Atlas M0 | — |
| Vectors | Qdrant Cloud (1 GB) | — |

No credit card on any of them. The browser only ever talks to the Vercel
origin; `/api/*` is rewritten to Render server-side, so the API URL above is an
implementation detail rather than something the client calls directly.

The app is two services and cannot go on Vercel alone: the client is a Next.js
app, the API is a long-running FastAPI process that streams SSE.

The steps below are what was actually done, in order, and re-running them
reproduces the deployment.

**1. Datastores.** Create a free MongoDB Atlas M0 cluster and a free Qdrant
Cloud cluster. From Atlas take the SRV connection string; from Qdrant take the
cluster URL and an API key.

**2. Schema and seed.** With `.env.local` filled in locally:

```bash
cd backend
uv run python -m scripts.migrate     # idempotent; safe on every deploy
uv run python -m scripts.seed        # idempotent; safe to re-run
```

**3. API → Render** (free web service, no card). Root directory `backend`.

```
Build:  pip install uv && uv sync --frozen
Start:  uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Set every server variable from the table above, plus `ENVIRONMENT=production`
so the session cookie is issued with `Secure`. Leave `CORS_ORIGINS` empty.

**4. Client → Vercel.** Import the repo, framework preset Next.js, root
directory the repo root. Set **one** variable: `BACKEND_URL`, pointing at the
Render service. No other secret belongs here — the client never talks to Mongo,
Qdrant or Gemini.

`next.config.ts` rewrites `/api/*` to `BACKEND_URL`, so the browser only ever
sees one origin. That is a security decision, not a convenience: on a split
origin the session cookie would need `SameSite=None; Secure` to be sent at all,
which discards the CSRF protection `SameSite` exists to provide and is
increasingly blocked as a third-party cookie. The cost is one network hop.

**5. Verify.** Re-run the isolation test against the production datastores:

```bash
cd backend && uv run python -m scripts.isolation_test
```

### The free-tier constraint that actually bites

Render's free tier spins a service down after **15 minutes** without traffic and
takes **about a minute** to wake. That interacts badly with Vercel's Hobby
limits, and the two paths through the app have *different* budgets:

| Path | Runs as | Hobby limit |
| --- | --- | --- |
| `/api/*` (chat SSE, uploads) | Vercel **proxied rewrite** to Render | **120s** — then `ROUTER_EXTERNAL_TARGET_ERROR` |
| Page renders (server components calling the API) | Vercel **function** | **10s default**, 60s maximum |

So a cold backend does not break chat — it breaks *page loads*, which is the
first thing a reviewer hits. Two mitigations, both in the repo:

- Every route that touches the API sets `export const maxDuration = 60`, the
  Hobby ceiling. Verify it survived the build:
  `cat .next/server/functions-config-manifest.json`.
- [`.github/workflows/keep-warm.yml`](.github/workflows/keep-warm.yml) pings
  `/api/health` every 10 minutes. Set the repo **variable** `API_URL` to the
  Render origin. This stays inside the allowance rather than evading it: Render
  grants 750 instance hours per month and a 31-day month is 744, so exactly one
  always-on free service fits. GitHub delays scheduled runs under load and
  disables them after 60 days of repo inactivity, so for a fixed review window
  add a second pinger at [cron-job.org](https://cron-job.org) (free, no card).

Because 750 hours covers only one service, the client has to be somewhere other
than Render — which is why it is on Vercel rather than both sitting together.

**Blueprint deploy.** [`render.yaml`](./render.yaml) declares the service, so
Render → New → Blueprint provisions it and prompts for each secret instead of
you setting fourteen variables by hand. `AUTH_SECRET` is generated by Render.

---

## Project layout

```
backend/
  app/
    config.py            validated at import; fails fast with a precise message
    errors.py            the error vocabulary — user message vs. log detail
    limits.py            per-workspace rate limiting for the shared quota
    auth/                session (JWT cookie) · workspace (the single gate)
    db/                  mongo (indexes) · repositories (workspace-bound) · schema
    vector/qdrant.py     THE shared collection — the only vector read path
    ingest/              extract · chunk · pipeline (idempotent, dual-store)
    gemini/              client (retry/backoff) · embed (asymmetric prefixes)
    rag/                 retrieve (hybrid + RRF) · prompt · agent (tool loop)
    tools/               registry (Pydantic = schema + validator) · execute (audit)
    routers/             auth · workspaces · documents · chat (SSE)
  scripts/               migrate · seed · isolation_test · smoke
  tests/                 chunking · fusion · tool safety · injection · limits · wire
src/
  proxy.ts               dashboard redirect gate (explicitly not the boundary)
  app/dashboard/         overview · chat · documents · tasks · activity
  components/            chat panel · retrieval panel · workspace switcher · …
sample-docs/             two corpora + the injection fixture
```

## Known limitations

- **Ingestion is synchronous**, bounded by a 4 MB upload cap. A job queue with a
  worker would be the right fix for large PDFs and partial-failure retries.
- **Scanned PDFs need OCR**, which isn't performed; they fail with a clear
  message rather than ingesting silently as empty.
- **Rate limiting is in-process.** With more than one API instance the effective
  limit multiplies by the instance count, and a restart forgets the window. The
  interface is shaped so a Redis-backed implementation is a drop-in.
- **No cross-workspace sharing.** Not attempted — default isolation is the
  graded property, and a half-wired opt-in would weaken it for no benefit.
- **Token counts depend on the provider reporting usage**; where it doesn't, the
  retrieval panel shows latency but omits tokens.
- **One Gemini free-tier quota is shared by every workspace.** The per-workspace
  limit stops one tenant monopolising it, but does not create more of it.

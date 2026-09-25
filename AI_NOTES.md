# AI_NOTES

## Tools, and how the work split

**Claude Code (Opus 5)** in the terminal, for essentially the whole build.

I drove architecture, the security model, and every judgement call about what
"correct" meant. The AI wrote most of the code against those decisions — and,
more valuably than I expected, did the API-surface archaeology: reading `.d.ts`
files and bundled docs to establish what the shipped libraries actually do, as
opposed to what they did a year ago.

| Mine | The AI's |
| --- | --- |
| Where the tenancy boundary sits, and that it must be un-bypassable rather than merely observed | Implementation: FastAPI routes, repositories, React components, the chunker |
| Chunking strategy and why not fixed-width | Reading type definitions to pin down current API shapes |
| Tool-context design (tools can't name a tenant) | The retrieval-debug and observability views |
| Making isolation an automated test rather than a claim | Test scaffolding once I'd said what to assert |
| Deciding a citation means "used", not "retrieved" | Porting the whole stack TypeScript → Python when I changed my mind on the backend |

My context file is [`CLAUDE.md`](./CLAUDE.md), which imports the
Next.js-generated [`AGENTS.md`](./AGENTS.md). Both are committed exactly as
used. `CLAUDE.md` is mostly a list of things the AI got wrong once and must not
get wrong again — it grew as a scar tissue file, which turned out to be its
real value.

## Three decisions I made myself

**1. Isolation is enforced by making the unsafe call impossible to write.**

I moved off the suggested Supabase/pgvector to Qdrant + MongoDB, and I want to
be straight about the cost: Postgres would have given row-level security, a
second boundary enforced by the database itself. Neither Qdrant nor MongoDB has
an equivalent, so **application code is the entire boundary here.** That raises
the bar rather than lowering it.

So `app/vector/qdrant.py` is the only module that imports the Qdrant client.
`search_chunks()` takes `workspace_id` as a required keyword argument and
builds the filter itself — there is no parameter through which a caller can
supply a filter, weaken one, or search unfiltered. "Forgot the workspace
filter" is not a mistake the API lets you express. Every returned point is
re-checked, and a foreign tenant **raises** rather than being quietly dropped,
because a silent filter would hide the one bug most worth knowing about. The
Mongo keyword arm puts `$text` and the tenant predicate in the same query
document, so both are evaluated server-side together — never a workspace filter
applied to an already-ranked list, which would compute the candidate set across
tenants and starve your top-k as other tenants grow.

`scripts/isolation_test.py` proves it against a live cluster rather than
asserting it in prose.

**2. No tool schema may name a tenant.**

`save_task` takes a title, details, priority and due date — and nothing else.
There is deliberately no `workspace_id` parameter anywhere in the registry. The
workspace arrives via `ToolContext`, built from the authenticated session. This
is what makes prompt injection boring instead of dangerous: a malicious
document can talk the model into *requesting* anything, but the worst reachable
outcome is a task saved in the workspace that document already lives in.
`tests/test_tool_safety.py` asserts this over the whole registry, so a tool
added later can't quietly reintroduce the hole.

**3. A citation means "the answer used this" — not "retrieval returned this".**

These had been conflated, and the result was that "the documents in this
workspace don't cover Project FALCON-7" arrived with eight sources listed
underneath it. Sources beneath a refusal read as evidence for an answer that
was never given. Citations are now filtered to the markers the answer actually
contains, and are empty otherwise; everything retrieved still appears in the
retrieval-debug panel, which is where "what did it look at" belongs. Clicking a
`[n]` marker opens the passage it drew on, so grounding is checkable in one
click instead of taken on trust.

## The hardest wrong turn the AI led me into

**Tool calling silently never fired, and everything looked fine.**

The AI wrote the Gemini streaming loop from its training data: send the
request, stream the text deltas, then read the function calls off the final
`interaction.completed` event. That is how the published prose docs describe
it. Every log line looked healthy — the model streamed a coherent answer, no
exception anywhere, HTTP 200 throughout. The only symptom was negative: ask it
to save a task, and it would cheerfully say it had, while the tasks table
stayed empty. **Nothing failed. A thing simply never happened.**

I noticed because I didn't trust the happy path — I checked the database rather
than the answer. The tool-call audit log had zero rows for a turn whose reply
claimed a task was saved.

The cause: when streaming, `interaction.completed` arrives with an **empty**
`steps` array. Function calls only exist as `step.start` events plus
`arguments_delta` events carrying JSON *string fragments* that have to be
reassembled. The finished object the AI was reading from is populated for
non-streaming calls only. The shipped `.d.ts` says so; the prose docs do not.

Fixing that surfaced a second failure immediately behind it. Feeding the tool
result back produced a bare `400 Request contains an invalid argument` — no
field, no hint. I reproduced three variants against the live API before finding
it: Gemini 3 reasoning models reject a follow-up turn containing a
`function_call` unless the `thought` signature that preceded it is replayed
too. The AI had been building a tidy, minimal history containing just the call
and its result. The fix is to replay the model's own steps verbatim, in
emission order, thought signatures included.

**What I took from it.** The AI's failure mode here wasn't bad code — it was
*confident, plausible, obsolete* code, and neither the type checker nor the
tests I had would catch it, because the code was internally consistent and the
API returned 200. So I added a standing rule to `CLAUDE.md`: for this SDK, the
type definitions are the source of truth and the prose docs are not, and any
claim about the API has to be verified against `node_modules` or the installed
package before it's written. That rule caught the next three problems before
they became bugs.

## Two smaller ones with the same shape

- **An API key in the logs.** The AI passed the Gemini key as a `?key=` query
  parameter, which the official quickstart shows. `httpx` logs full request
  URLs at INFO, so the key was being written verbatim into application logs and
  would have gone straight into any aggregator. Moved to an `x-goog-api-key`
  header. The brief's "never exposes secrets, not in logs" is why I went
  looking for it rather than assuming.
- **camelCase outside, snake_case inside.** Pydantic applies `alias_generator`
  per model, never down the tree — something the AI and I both assumed was
  recursive. Nested storage models inside a wire model leaked snake_case, so
  `citation.chunkId` read `undefined` in the browser. It surfaced only as a
  React "each child needs a unique key" warning pointing at a line that
  *had* a key. The tempting one-line fix (alias the storage model) would have
  silently started writing camelCase keys into MongoDB. A mismatch across a
  language boundary is invisible to both type checkers, so it now has a test
  that walks the generated OpenAPI document and fails on any snake_case key at
  any depth.

## What I'd do with more time

- **Deploy is the gap.** Everything runs and is tested locally; the live URL is
  the remaining work, and I know that's the first thing graded.
- **A re-ranker.** Hybrid RRF fixed the exact-identifier problem, but a
  cross-encoder over the fused top-20 would do better on paraphrased questions.
- **Cancellation.** Stopping generation mid-stream currently cancels the server
  generator without marking the message complete, so I left the Stop button out
  rather than ship a row stuck in `streaming` forever.
- **Evals over anecdotes.** A fixed question set scoring grounding, refusal, and
  citation correctness on every push. Right now `scripts/smoke.py` checks those
  behaviours once, by hand.
- **Per-workspace embedding budget**, so one tenant can't exhaust a shared free
  tier for everyone.

## The one instruction that changed the collaboration

From `CLAUDE.md`, after the tool-calling episode:

> **Verify against reality, not memory.** This stack moved after the training
> cutoff, and guessing has already produced bugs here. The published prose docs
> disagree with the shipped package; **the type definitions are the source of
> truth.** When unsure, read the `.d.ts` or the bundled docs. Do not infer an
> API from what it looked like previously.

Underneath it I kept a running list of specific findings — `middleware.ts` is
now `proxy.ts`, `params` is a Promise, `motor` is deprecated in favour of
`pymongo.AsyncMongoClient`, `gemini-embedding-2` has no `taskType`, passing
bare strings to `embed_content` returns one aggregated vector rather than N.
Each line is a bug that happened once. Writing them down was the difference
between the AI being fast and the AI being reliable.

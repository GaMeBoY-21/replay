# Replay

A flight recorder and a rewind button for AI agents.

## The problem

An agent does something wrong and you want to know why. Running it again does not answer the question: the model
samples differently, takes another path, and the failure you were studying is gone. A transcript of prompts and
answers does not answer it either — it shows what the agent said, not which earlier step produced the wrong answer,
and by the time the mistake is visible in the output it is usually many steps downstream of its cause.

Aviation settled this a long time ago. When a plane crashes, nobody flies it again to see what happens. They read
the recorder.

## What Replay does

- **Records** every non-deterministic input a run receives, so the run can be replayed exactly.
- **Replays** it with the model blocked from being called: the same steps, the same answer, in milliseconds.
- **Traces** an answer backwards through what each step read and wrote, to the write it actually rests on.
- **Forks** a run at one step, changes a single decision, and lets everything after it run live.
- **Halts** a run that loops or runs too long, and resumes it with the ceiling raised.

## How it works

**One gate.** Everything non-deterministic an agent touches — a model call, a tool call, the clock, randomness —
goes through one function. It assigns the effect a sequence number, appends a *requested* event, runs it, appends a
*completed* event with the result, and returns. The log is append-only, and a run's answer is a function of its
inputs, so recording the inputs is enough to reproduce it.

**Replay feeds the log back.** In replay mode the same gate returns the recorded result instead of executing
anything, matching each request against the log by shape. If the agent asks for something the recording does not
contain — different arguments, a different call — that is a divergence and it fails loudly rather than quietly
calling the model. The provider is replaced by one that raises if it is ever called, so "it replayed" cannot be a
coincidence.

**The trace follows reads and writes.** Each step's writes to agent memory record which reads they were derived
from. Flagging an answer walks that graph backwards, earliest first, and stops at a write that read nothing before
it. That write is the origin. For the demo run it is `invoice.currency = "USD"` at step 10, written by the tool
whose job is to record what the agent concluded — four steps after the run had already read the evidence that said
otherwise, and four steps before the answer that made the mistake visible.

**A fork is a replay that stops believing the log.** Replay the parent up to the chosen step, substitute one
recorded result, and run live from there. The child's log holds only its own events; the shared prefix stays in the
parent's log and is read, not copied, so a fork costs what it adds. A diff shows that prefix as shared by storage
rather than merely identical.

**Breakers.** Repeated identical calls, effect depth, token budget and latency are checked at the same gate. A trip
appends a refusal and marks the run halted, which is also how an operator's cancel ends a run: the log always ends
with a reason rather than stopping mid-effect, because a log that just stops is what a crash looks like.

## The experiment

One task, eleven runs, one model (qwen2.5:14b through Ollama), same tools and prompt every time: reconcile an
invoice and report the total in USD. The invoice is in rupees and nothing states it; the only evidence is the
payment instructions, which name an Indian bank. The right answer is $492.

Six runs got it right. Five got it wrong, in four different ways: `USD` → $41,000, `CAD` → $29,930 twice, `EUR` →
$44,280, and one run that wrote a template placeholder as the currency and never converted at all. Each run also
records *why* it chose a currency, and those reasons turn out to be unreliable in both directions: the run that
answered $41,000 had already read the Indian bank details and called its choice "Assumption based on standard
business practices", while five of the six correct runs cite records that do not contain what they claim — one
cites a field that does not exist. The log is trustworthy; the explanation is not.

Tracing the wrong run's answer lands on the currency write, and forking that one decision — serving the response a
different run gave at the same step — produces $492, with the tools executing for real from that point.

## AWS

| Service | Why |
|---|---|
| **DynamoDB** (events) | The log. One partition per run, sort key per event, conditional append, so two writers can never claim the same event id. Streams make projection a consequence of writing. |
| **DynamoDB Streams → Lambda** (projector) | Rebuilds a run's summary from its log whenever it changes. Idempotent by reconstruction, which is what at-least-once, unordered delivery requires. |
| **DynamoDB** (views) | What the run list reads: summaries and fork trees, so listing never scans the log. |
| **S3** (payloads) | A recorded model response can exceed DynamoDB's item limit; anything over 100 KB is written to S3 and referenced. |
| **Lambda** (api) | The API: read-only over both tables, replay-only, and — without CloudFront — the static frontend too. |
| **API Gateway** (HTTP API) | One origin for the app and the API, throttled. |
| **CloudFront + S3** | In the stack behind a flag, off until AWS verifies this account for CloudFront. |
| **IAM, CloudWatch Logs, Budgets** | Least privilege per function, 7-day retention, and a USD 5 budget alert on a credit account. |
| **CDK** (Python) | The stack is code; `cdk destroy` leaves nothing behind. |

## What was built during the event

Everything in this repository except the design documents it was built against. The kernel and its gate, the
Strands integration, the three stores behind one protocol, fork and resume, the provenance trace and the diff, the
breakers, the API and projector, the local server, the frontend, the AWS stack — and the corpus: every run was
recorded during the event against a local model, including the ones that failed, which are kept as evidence rather
than deleted.

The suite is 542 passing tests and 3 skipped (runs from an earlier corpus that never finished, so there is nothing
to replay). Every load-bearing claim in the documentation has a test behind it, and `make verify` breaks each claim
on purpose and requires the suite to go red for it: 135 claims, none unguarded.

## AI tools used

- **Claude Code (Anthropic)** — wrote the code, the tests and the infrastructure under my direction, in a session I
  drove: I set each stage's goal and its gate, reviewed the work, and made the calls when the evidence contradicted
  the plan.
- **qwen2.5:14b, via Ollama** — the agent under test. It produced every recorded run in the corpus. It is not part
  of the product; the deployment has no model.
- **Strands Agents SDK** — the agent framework the recorder integrates with, through its model and tool seams.

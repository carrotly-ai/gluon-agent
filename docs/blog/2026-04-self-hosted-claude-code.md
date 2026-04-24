# Why we built Gluon: self-hosted Claude Code orchestration on your own backend

**Status**: Draft
**Date**: 2026-04-25
**Audience**: engineers evaluating Claude Code orchestrators
**Publication target**: Medium + dev.to + r/ClaudeAI + HN

---

## The change nobody announced

On April 21st, Claude Pro quietly lost Claude Code access. The pricing page
stopped listing it as a Pro feature; the support article was renamed from
"Using Claude Code with your Pro or Max plan" to just "Max plan." When users
noticed, Anthropic's response was "it's a small test on 2% of new signups."
The public-facing site already reflected the change.

It was the third tightening in as many weeks: third-party tool access got
cut for subscribers, enterprise users were forced onto per-token billing,
and now Claude Code itself is heading toward the $100/mo Max tier.

Good engineering organizations survive moments like this not because they
predict pricing moves but because they built on something they control.
That's the case Gluon has been making for a year.

## What Gluon actually is

Gluon is a self-hosted orchestrator for Claude Code. You run it in Docker on
your own server. It gives you:

- A long-running process that manages multiple concurrent Claude Code
  sessions across different projects
- Four ways to talk to it: CLI, web dashboard, Telegram bot, Discord bot
- Session persistence — every run is tracked, resumable, replayable
- Git worktree isolation per run with garbage collection so disks don't fill
- A witness system that detects stuck runs and nudges them back on track
- Background execution with a self-propelling work queue
- Cost tracking + per-run caps + (as of this week) operator-settable defaults

None of that depends on Claude Pro. Gluon invokes Claude through whichever
backend your organisation already pays for — **AWS Bedrock**, **Google
Vertex AI**, **Microsoft Foundry** (Azure AI Foundry), or the **direct
Anthropic API** / Claude CLI subscription. Switch with a single command
(`gluon provider vertex`), an env var, or a toggle in the web dashboard
Settings page. When Anthropic's subscription model shifts, you can pivot
to Bedrock or Vertex in ten seconds without touching code.

## Why multi-backend, specifically

The standard "use Claude Code" story ties you to Anthropic's direct API
pricing and reliability. That's fine for a lot of teams. For the rest,
running Claude through a cloud provider you already use adds three things
the direct API can't:

1. **Pricing is per-token at published rates from whichever cloud.** No
   subscription tiers. You set budgets, track spend per run, and can
   predict the monthly bill against the AWS/GCP/Azure invoice you already
   process.
2. **Your data stays inside your existing cloud account.** Gluon's
   container reads your cloud credentials from the host (`~/.aws`,
   `~/.config/gcloud`, `~/.azure`); prompts and responses travel between
   you, your cloud's Claude endpoint, and Anthropic's inference backbone.
   Nothing touches `console.anthropic.com` unless you want it to.
3. **Enterprise controls already exist.** IAM policies, VPC endpoints,
   audit logging, tag-based cost allocation, Entra ID / managed identity
   — the things your security team wants — are already in your cloud.
   No separate admin surface.

The technical story is almost boring. Gluon's `llm_provider.py` wraps the
official `anthropic` SDK's four client classes (`AsyncAnthropicBedrock`,
`AsyncAnthropic`, `AsyncAnthropicVertex`, `AsyncAnthropicFoundry`) behind
a common interface. Each provider contributes its own `CLAUDE_CODE_USE_*`
flag to the subprocess environment when Gluon spawns Claude Code. Model
IDs get resolved to the right format per backend automatically
(`global.anthropic.claude-sonnet-4-6` on Bedrock, `claude-sonnet-4-6` on
Vertex or Anthropic direct, etc.) — the same `--model sonnet` works
everywhere. Bedrock is the default; everything else is opt-in.

## The three things that are actually hard

"Just run Claude Code in a tmux session" is a reasonable first pass. What
pushes teams toward an orchestrator is usually one of three problems.

### 1. Session resume across process restarts

When `claude` crashes mid-task — or you Ctrl-C a long run, or the container
gets restarted during a deploy — you lose the session unless something
captured its ID. Gluon captures every Claude session ID and stores it in
SQLite alongside the ExecutionRun record. A resume is:

```bash
gluon resume abc1234
```

Under the hood Gluon uses the SDK's `fork_session` option to branch from
the last known state. The old session transcript stays intact; the new
session inherits the context. You can run three resume branches
concurrently if you want to try different fixes.

This is hard to get right in your own code. The SDK surface is stable but
there are edge cases around parent-child session chains, stale JSONL files,
and concurrent forks that we've spent months sanding.

### 2. Git isolation

Two Claude agents working on the same project directory will step on each
other within minutes. Gluon spawns each run in its own git worktree,
branched from a frozen commit, so concurrent work is always isolated. When
a run finishes, the worktree either becomes a PR or is garbage-collected
after a retention window (default 7 days; configurable).

The GC is less glamorous than the worktree part but it matters: without it,
a server running Gluon for a month accumulates dozens of stale worktrees
and fills the disk. We learned this the embarrassing way.

### 3. Unsupervised runs need observability and auto-recovery

If a Claude agent is going to work for hours unattended, you need to know
when it's stuck. Gluon has a **witness** subsystem that periodically
classifies run health using Haiku (cheap model) as one of: HEALTHY, SLOW,
STUCK, LOOPING, NEEDS_CONTEXT_RESET, ZOMBIE. Each classification maps to a
recovery action: NONE, NUDGE, RESTART, or ESCALATE.

As of this week the NUDGE action is wired end-to-end. When a run is
classified as LOOPING, the witness injects a course-correction message into
the live session — *"you appear to be stuck in a retry loop; stop, reassess,
and try a different approach"* — via the same queued-followup mechanism the
web UI uses. There's a 15-minute cooldown so a stuck run doesn't get spammed.

This is the kind of thing that only matters when you trust an agent enough
to leave it running. And that trust is only buildable once you can see what
the agent is doing.

## What Gluon is not

A few things we deliberately don't try to be:

- **Not a Claude Code replacement.** We shell out to the SDK. When
  Claude Code gets better, Gluon gets better.
- **Not multi-model.** We run Claude — the four tiers our CLAUDE.md locks
  to (Opus 4.6 / 4.5, Sonnet 4.6, Haiku 4.5) — on any of four cloud
  backends. Adding "works with GPT-4 too" would dilute the integration
  story: Claude-specific features like the SDK's hooks, `fork_session`,
  and witness-class Haiku calls don't have clean equivalents on other
  vendors. The value is *one Claude abstraction, backend flexibility*,
  not "any LLM."
- **Not a SaaS.** There's no hosted Gluon. We publish the Docker image, you
  run it. If you want the SaaS experience, use Anthropic's web UI.
- **Not a team product yet.** Gluon today assumes one user. Per-user auth
  and budgets are on the roadmap but not shipped.

## Getting started

If you have credentials for any of the four backends and a machine with
Docker:

```bash
curl -fsSL https://raw.githubusercontent.com/carrotly-ai/gluon-agent/main/docker-compose.yml -o docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/carrotly-ai/gluon-agent/main/.env.example -o .env
# Edit .env — set PUID, PGID, GH_TOKEN, GIT_USER_NAME, GIT_USER_EMAIL
# Then pick ONE provider block (Bedrock is the default):
#   Bedrock   → AWS_REGION + AWS_BEARER_TOKEN_BEDROCK (or standard AWS creds)
#   Anthropic → ANTHROPIC_API_KEY (or reuse your `claude login` at ~/.claude)
#   Vertex    → ANTHROPIC_VERTEX_PROJECT_ID + CLOUD_ML_REGION + `gcloud` ADC
#   Foundry   → ANTHROPIC_FOUNDRY_RESOURCE + API key (or `az login` via Entra ID)
docker compose up -d
open http://localhost:45866
```

That's the full install. No CLI install, no pip requirements. If you
pick Bedrock and you're missing the Model Garden approval step
(Anthropic > Sonnet/Opus/Haiku), AWS will prompt you in the console.
Vertex has the same model-garden gate. Foundry needs deployments created
in the Foundry portal matching the tier names.

## What's shipped in the last month

The three themes the earlier draft of this post pointed at as "next" all
landed in v0.10.0:

1. **Multi-agent coordination.** Persistent `Agent` identities, a task
   queue with atomic `BEGIN IMMEDIATE` checkout so two agents can't
   claim the same work, cron-based heartbeats, and per-agent monthly
   budgets with a `BudgetExceededError` that fires before a run starts.
2. **Observability & replay.** A "Timeline" tab that shows one dot per
   tool call on a horizontal strip — click a dot or press ←/→ to step
   through the run, with the full inputs and the agent's reasoning for
   each call surfaced inline. A "Tools" tab with the per-run tool-usage
   breakdown. Reasoning threading so every tool call is one click away
   from *"here's what the agent was thinking right before it ran this."*
3. **Trust & control.** Approval gates on risky tool calls (25+ patterns
   like `rm -rf`, `git push --force`, `npm publish`) with two-way
   Telegram / Discord approve/deny buttons and a web-dashboard queue.
   Per-workspace daily and monthly budgets. Hard per-run caps on tool
   calls and wall-clock duration, enforced by a PreToolUse hook and a
   watchdog.

The one item from that list still open is **per-user auth**. That's the
big Theme D5 push — designed-before-coded, intentionally not rushed, and
the last thing between Gluon and a team deployment.

## What's next

- **Cut-and-shut polish** on the observability views — per-tool cost
  attribution once the SDK exposes it, cumulative-cost axis on the
  Timeline, cross-session search
- **Multi-user auth (D5).** OIDC against whatever your organisation
  already uses, per-user budgets, RBAC for approval policies.
- **Mobile experience.** The PWA works; native shell around it is the
  open question.

Everything that ships first goes into the public Docker image at
`ghcr.io/carrotly-ai/gluon-agent:latest`, with tagged releases
(`:v0.10.0`, etc.) for pinning.

## If you want to try it

- Repo: https://github.com/carrotly-ai/gluon-agent
- Issues, PRs, and "I hit this weird thing" reports are welcome.

If you're currently paying for Claude Pro *primarily* for Claude Code, the
pricing page update is a nudge to look at what else is possible. Self-hosted
isn't for everyone. But for engineers who already have AWS, GCP, or Azure,
the math has quietly flipped — and if you just want the direct API without
a subscription, that's now a one-line toggle in Gluon too.

---

*Written by the Gluon team. We build software development tools for teams
that want AI in the loop without handing the whole pipeline to someone
else's cloud.*

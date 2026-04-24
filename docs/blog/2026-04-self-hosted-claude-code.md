# Why we built Gluon: self-hosted Claude Code orchestration on AWS Bedrock

**Status**: Draft
**Date**: 2026-04-24
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

None of that depends on Claude Pro. Gluon invokes Claude through **AWS
Bedrock** using your own AWS credentials. When Anthropic's subscription
model shifts, nothing about Gluon changes.

## Why Bedrock, specifically

Bedrock is not a workaround. For teams with any AWS footprint it's the
better place to run Claude for three reasons:

1. **Pricing is per-token at published rates.** No subscription tiers. You
   set budgets, track spend per run, and can predict the monthly bill.
2. **Your data stays inside your AWS account.** Gluon's container reads
   your `~/.aws/credentials`; prompts and responses go between you, Bedrock,
   and Anthropic's inference endpoints. Nothing touches
   `console.anthropic.com` unless you want it to.
3. **Enterprise controls already exist.** IAM policies, VPC endpoints, CloudTrail
   logging, tag-based cost allocation — the things your security team wants
   — are already in Bedrock. No separate admin surface.

The technical story is almost boring: Gluon's agent layer uses the
`claude-agent-sdk` which speaks to Bedrock via the standard
`AnthropicBedrock` client. Bedrock gives you the same Claude models
(Sonnet 4.6, Opus 4.6, Haiku 4.5). The abstraction is tight and the
behavior is identical.

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
- **Not multi-provider.** We run on Bedrock with the four Claude models our
  CLAUDE.md locks to. Adding "works with OpenAI too" dilutes the integration
  and breaks the value proposition.
- **Not a SaaS.** There's no hosted Gluon. We publish the Docker image, you
  run it. If you want the SaaS experience, use Anthropic's web UI.
- **Not a team product yet.** Gluon today assumes one user. Per-user auth
  and budgets are on the roadmap but not shipped.

## Getting started

If you have an AWS account with Bedrock access and a machine with Docker:

```bash
curl -fsSL https://raw.githubusercontent.com/carrotly-ai/gluon-agent/main/docker-compose.yml -o docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/carrotly-ai/gluon-agent/main/.env.example -o .env
# Edit .env — set PUID, PGID, GH_TOKEN, GIT_USER_NAME, GIT_USER_EMAIL, AWS_BEARER_TOKEN_BEDROCK
docker compose up -d
open http://localhost:45866
```

That's the full install. No CLI install, no pip requirements, no Anthropic
subscription. If you're missing the Bedrock model-access approval step
(Anthropic > Sonnet/Opus/Haiku), AWS will prompt you in the console.

## Where Gluon is going

Three themes in the next 6 months, in priority order:

1. **Multi-agent coordination.** Task-level atomic checkout so two agents
   can't claim the same work. File-level locks. An inbox model. Agent
   identities with per-agent budgets and schedules. This is the hot new
   category and we have Paperclip-inspired designs ready to implement.
2. **Observability & replay.** Scrub-backwards timelines, per-tool cost
   breakdowns, the reasoning chain that led to each tool call. Tools
   spend 60% of their time on context (Anthropic's 2026 Agentic Coding
   Trends report); showing that breakdown is itself a feature.
3. **Trust & control.** Approval gates for risky tool calls (push, delete,
   publish) with two-way Telegram/Discord confirmation. Per-user auth so
   Gluon can be deployed to a team server. Hard per-run caps on tokens,
   tool calls, and duration.

Everything that ships first goes into the public Docker image.

## If you want to try it

- Repo: https://github.com/carrotly-ai/gluon-agent
- Issues, PRs, and "I hit this weird thing" reports are welcome.

If you're currently paying for Claude Pro *primarily* for Claude Code, the
pricing page update is a nudge to look at what else is possible. Self-hosted
isn't for everyone. But for engineers who already have AWS, the math has
quietly flipped.

---

*Written by the Gluon team. We build software development tools for teams
that want AI in the loop without handing the whole pipeline to someone
else's cloud.*

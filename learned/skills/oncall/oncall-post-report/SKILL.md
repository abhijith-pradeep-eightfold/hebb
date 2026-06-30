---
name: oncall-post-report
model: sonnet
description: Post a finished oncall investigation report back to the PagerDuty Slack thread. Always drafts BOTH a concise threaded reply AND the full table-structured report (Slack Canvas), then asks the user which to post — both (Canvas + linking reply) or reply-only — leaning reply-only for a small RCA where a full Canvas would be noise. Use as the final step of any oncall ticket (queue backed up, Solr CPU too high, Redis errors, etc.) once the investigation is done and the user asks to "post the report in Slack" / "share this in the PD thread" / "post to the oncall channel". Encodes the safety rules every outward-facing oncall post must follow: draft both forms and ask which to post, confirm the destination/surface before posting, and render every person/team/customer reference as plain text (never an @-mention) so the post pages no one.
knowledge_required:
  - "[[../../../wiki/oncall/oncall-investigation|Oncall investigation — ticket types]]"
knowledge_optional:
  - "[[../../../wiki/oncall/queue-backed-up|Queue backed up (oncall)]]"
  - "[[../../../wiki/oncall/solr-cpu-high|Solr CPU too high (oncall)]]"
  - "[[../../../wiki/oncall/alarm-provisioning-failures|Alarm Provisioning Failures (oncall)]]"
  - "[[../../../wiki/oncall/rds-cpu-high|RDS CPU too high (oncall)]]"
  - "[[../../../wiki/oncall/redis-errors-detected|Redis Error Detected (oncall)]]"
  - "[[../../../wiki/oncall/airflow-dag-failure|Airflow DAG Failure (oncall)]]"
---

# Post an oncall report to Slack

The final, outward-facing step of an oncall investigation: take the assembled **table-structured report** (built per [[../../../wiki/oncall/oncall-investigation#reporting-an-oncall-ticket|Oncall investigation → reporting]]) and publish it back to the PagerDuty alert's Slack thread. This skill is **MCP-only** (Slack tools) — there is no bundled script and nothing to run against `$CODE_BASE`.

It is a thin, reusable final step for **every** oncall ticket type — the per-type runbooks (e.g. `oncall-queue-backed-up`) name it as their last step.

## Three non-negotiable safety rules

An oncall report names an on-call individual and customer tenants, and posting is outward-facing. All three rules below are **required behavior** — do not skip them:

1. **Draft BOTH forms, then ask which to post — lean reply-only for a small RCA.** Always prepare **both**:
   - a **concise threaded reply** (a few lines: what fired, the verdict, the action), and
   - the **full table-structured report** as a Slack Canvas.

   Then ask the user **which to post**: **both** (Canvas + a linking threaded reply) or **reply-only** (just the concise reply, no Canvas). **Lean toward reply-only for a small RCA** — a transient blip, a quick ack-and-close, or any finding that is a few lines: a full Canvas there is noise, not signal. Reserve the Canvas for a substantial investigation (a multi-driver breakdown, a lineage trace, a write-storm). **Do not default to creating a Canvas** — present both drafts and let the user choose. (This is the small-RCA default; the user can always ask for both.)
2. **Confirm the destination/surface before posting.** The initial instruction ("post the report in Slack") almost never says *where*. Confirm the destination together with the both-vs-reply-only choice (rule 1) before any post:
   - **PagerDuty alert thread** (the usual case) — the reply (and the Canvas, if posting both) go in the alert thread.
   - **New message in a named channel** — if the report should go somewhere other than the alert thread.
   Use `AskUserQuestion` (or an equivalent explicit confirmation) and wait for the answer. Do **not** guess the surface or the form.
3. **Plain text, never @-mentions.** Render every person, team, and customer reference (`hpatel@eightfold.ai`, `@EightfoldAI/core-search`, `lockheedmartin.com`, …) as **plain text**, not a Slack `@`-mention or `<@U…>`/`<!subteam…>` token. The post must not page or notify anyone it names — it is a record, not an escalation.

## Steps

1. **Locate the alert thread.** From the originating PagerDuty Slack alert you should already have the channel id and the parent message timestamp (`thread_ts`). If not, find the PD alert message (the external-context step that opened the ticket recorded it).
2. **Draft both forms** (rule 1). Write the concise threaded reply **and** the full table-structured report (Canvas body), with all owner/customer references in plain text (rule 3). Show **both drafts** to the user.
3. **Ask which to post, and confirm the surface** (rules 1–2). Ask **both** the form (both vs reply-only) and the destination, leaning your suggestion toward **reply-only for a small RCA**. Wait for the user's explicit choice; if the wording needs revising, revise and re-show before posting. Do **not** post until the user approves the exact wording.
4. **Post the chosen form.**
   - *Reply-only (the small-RCA default):* post the concise threaded reply via `slack_send_message` (`channel_id` = the alert channel, `thread_ts` = the parent ts). **No Canvas.**
   - *Both:* create the Canvas with the full report via `slack_create_canvas` (title = the ticket, body = the table-structured report in Canvas markdown), then post the **concise** threaded reply via `slack_send_message` carrying the verdict, who to route to, and the Canvas link.
   - *New channel message:* `slack_send_message` to the named channel (resolve it with `slack_search_channels` if you only have a name).
   In every branch, keep owner/customer references plain text (rule 3).
5. **Report back the links.** Return the Canvas URL (if created) and the posted message URL so the user has both.

## Notes

- This skill carries **runtime judgment** (the draft-both/ask-which/confirm-surface branches), so it is a skill, not a script. There is no `scripts/` directory — the work is Slack MCP calls.
- It composes after the investigation: the per-type runbook (`oncall-queue-backed-up`) and the umbrella discipline ([[../../../wiki/oncall/oncall-investigation|Oncall investigation]]) both name it as the closing step.

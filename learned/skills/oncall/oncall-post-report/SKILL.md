---
name: oncall-post-report
model: sonnet
description: Post a finished oncall investigation report back to the PagerDuty Slack thread — create a Slack Canvas with the full table-structured report and reply with a concise summary in the alert thread. Use as the final step of any oncall ticket (queue backed up, Solr CPU too high, etc.) once the investigation is done and the user asks to "post the report in Slack" / "share this in the PD thread" / "post to the oncall channel". Encodes two safety rules every outward-facing oncall post must follow: confirm the destination/surface before posting, and render every person/team/customer reference as plain text (never an @-mention) so the post pages no one.
knowledge_required:
  - "[[../../../wiki/oncall/oncall-investigation|Oncall investigation — ticket types]]"
knowledge_optional:
  - "[[../../../wiki/oncall/queue-backed-up|Queue backed up (oncall)]]"
  - "[[../../../wiki/oncall/solr-cpu-high|Solr CPU too high (oncall)]]"
  - "[[../../../wiki/oncall/alarm-provisioning-failures|Alarm Provisioning Failures (oncall)]]"
---

# Post an oncall report to Slack

The final, outward-facing step of an oncall investigation: take the assembled **table-structured report** (built per [[../../../wiki/oncall/oncall-investigation#reporting-an-oncall-ticket|Oncall investigation → reporting]]) and publish it back to the PagerDuty alert's Slack thread. This skill is **MCP-only** (Slack tools) — there is no bundled script and nothing to run against `$CODE_BASE`.

It is a thin, reusable final step for **every** oncall ticket type — the per-type runbooks (e.g. `oncall-queue-backed-up`) name it as their last step.

## Two non-negotiable safety rules

An oncall report names an on-call individual and customer tenants, and posting is outward-facing. Both rules below are **required behavior** — do not skip them:

1. **Confirm the destination/surface before posting.** The initial instruction ("post the report in Slack") almost never says *where* or *in what form*. Ask the user to confirm before any post — offer the concrete options:
   - **Canvas in the PagerDuty alert thread** (best for a long table-structured report) — a Canvas plus a short threaded reply linking it.
   - **Markdown message in the PagerDuty alert thread** — the report inline as a threaded reply.
   - **New message in a named channel** — if the report should go somewhere other than the alert thread.
   Use `AskUserQuestion` (or an equivalent explicit confirmation) and wait for the answer. Do **not** guess the surface.
2. **Plain text, never @-mentions.** Render every person, team, and customer reference (`hpatel@eightfold.ai`, `@EightfoldAI/core-search`, `lockheedmartin.com`, …) as **plain text**, not a Slack `@`-mention or `<@U…>`/`<!subteam…>` token. The post must not page or notify anyone it names — it is a record, not an escalation.

## Steps

1. **Locate the alert thread.** From the originating PagerDuty Slack alert you should already have the channel id and the parent message timestamp (`thread_ts`). If not, find the PD alert message (the external-context step that opened the ticket recorded it).
2. **Confirm the surface** (rule 1 above). Branch on the user's choice.
3. **Post.**
   - *Canvas in PD thread:* create the Canvas with the full report via `slack_create_canvas` (title = the ticket, body = the table-structured report in Canvas markdown). Then post a **concise** threaded reply via `slack_send_message` (`channel_id` = the alert channel, `thread_ts` = the parent ts) carrying the root cause, who to route to, and the Canvas link.
   - *Markdown reply in PD thread:* post the report inline as a threaded reply via `slack_send_message` (`thread_ts` set).
   - *New channel message:* `slack_send_message` to the named channel (resolve it with `slack_search_channels` if you only have a name).
   In every branch, keep owner/customer references plain text (rule 2).
4. **Report back the links.** Return the Canvas URL (if created) and the posted message URL so the user has both.

## Notes

- This skill carries **runtime judgment** (the confirmation branch), so it is a skill, not a script. There is no `scripts/` directory — the work is Slack MCP calls.
- It composes after the investigation: the per-type runbook (`oncall-queue-backed-up`) and the umbrella discipline ([[../../../wiki/oncall/oncall-investigation|Oncall investigation]]) both name it as the closing step.

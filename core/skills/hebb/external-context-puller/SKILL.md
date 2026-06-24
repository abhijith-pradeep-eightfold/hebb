---
name: external-context-puller
description: When the prompt references an external thread or ticket — a Slack link, a Jira issue key/URL, a Confluence/Drive/Gmail link — read it via MCP and pull the relevant context. Use at the start of a task to gather requirements, decisions, and linked items behind such a reference.
---

# Pull external context

When the task points at something outside the repo — a Slack thread, a Jira ticket, a Confluence page — fetch it and extract what matters before doing the work.

## Steps
1. **Scan the prompt and user messages** for external references:
   - Jira issue keys (e.g. `ABC-1234`) or `*.atlassian.net/browse/...` URLs
   - Slack message/thread URLs (`*.slack.com/archives/<channel>/p<ts>`)
   - Confluence page URLs, Google Drive/Docs links, Gmail threads
2. **Load the MCP tool you need.** These tools are deferred — run `ToolSearch` first to load their schemas, then call them:
   - Jira: `getJiraIssue`, `searchJiraIssuesUsingJql`, `getJiraIssueRemoteIssueLinks`
   - Slack: `slack_read_thread` (needs channel + thread ts, parsed from the URL), `slack_read_channel`
   - Confluence: `getConfluencePage`; Drive: `read_file_content`; Gmail: `get_thread`
3. **Fetch and extract** the context relevant to the task: the ask, acceptance criteria, decisions in the discussion, and linked tickets/PRs worth following.
4. **If a fetch fails** (auth missing in a headless run, no access), record the symptom — don't guess the contents.

Record via `log-appender` what you pulled (source + identifier) and the context it gave you.

# Summarize a workflow change in plain English

You are writing the `human_summary` for a Modify Agent change log entry. The reader is a non-technical operator (Zaki) who needs to know:
1. What changed in the workflow
2. Whether they need to do anything (e.g. tell external callers about a new webhook URL)

## Workflow

Name: {workflow_name}

## User's request

{user_request}

## Edits applied

```json
{edits_json}
```

## Your response

Write 1-3 sentences in plain English. Lead with what changed; if there's a downstream impact (new URL, new schedule, behavior change visible to users), mention it. No JSON, no markdown formatting, no preamble.

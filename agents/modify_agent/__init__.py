"""n8n Modify Agent — surgical edits to live workflows with snapshot+rollback.

See specs/modify-agent-spec.md for the full design.

Phase order: FETCH → CLASSIFY → PLAN → SNAPSHOT → APPLY → TEST → AUDIT → HARDEN → DEPLOY
ROLLBACK fires on any failure from APPLY onward.
"""

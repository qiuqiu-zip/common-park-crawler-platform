# Codex Workflow

This project is developed by scoped Feature rounds.

## Feature Scope

Each round has:

- a `RUN_ID`
- a Feature name
- allowed work
- forbidden work
- acceptance commands
- a result review
- a final summary

A Feature PASS means that Feature's scope passed. It does not mean final
full-project acceptance has been performed.

## Review Loop

Codex must ask the ChatGPT supervision conversation for the next
`NEXT_CODEX_INSTRUCTION`. Codex should not infer the next Feature from local
files, README text, git state, or previous work.

Expected stages:

```text
TASK_EXTRACTION
EXECUTE_INSTRUCTION
RESULT_REVIEW
FINAL_USER_SUMMARY
PASS or PAUSE
```

## Result Format

Each implementation result should include:

- `RUN_ID`
- Feature name
- changed files
- validation commands and results
- added tests and coverage
- environment notes
- explicit statement that the result is scoped to the current Feature

## User Confirmation

User confirmation is required when the supervisor asks for it, when a task would
change scope, or when an external state change is needed. Otherwise Codex should
continue implementation and verification.

## Project Constraints

- Do not add runtime database dependencies.
- Do not make default tests access real external network targets.
- Do not enter the next Feature without a new supervision instruction.
- Do not describe one Feature PASS as final project acceptance.

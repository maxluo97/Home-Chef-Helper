# CLAUDE.md

Working preferences for Claude in this repository.

## Git

- Claude may commit, push, pull, and diff without asking first.
- Be careful when merging two branches or two forks, especially when they differ a lot. Look at the differences and check with the user before merging.
- Every commit needs a clear, useful message that says what changed and why.
- Releases and tags need messages that describe the overall work that went into that release or tag.

## Destructive actions

- Do not take any action that would delete more than 20% of a codebase, local or remote, without explicit permission. In more than 99% of cases such an action would be inappropriate, so treat a plan that leads to it as a sign that something is wrong.

## Naming

- Every variable must have a meaningful name, including counters and loop indices. Do not use `i`, `n`, `j`, or similar.
  - Example: a loop that counts marbles uses `iMarble` (index) or `nMarble` (count), not `i` or `n`.

## Comments

- Comment code well.
- Give every major code block a comment describing what it does.
- Use in-line comments where they help.
- Lean toward more comments rather than fewer, without becoming excessive.

## Design decisions

- For major design decisions, ask the user for clarification instead of making assumptions.

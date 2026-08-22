# User & Workspace Guidelines for Antigravity

## Proactive Customization & Automation (CRITICAL)
- **Always offer to package recurring workflows into Skills, Plugins, or MCPs:**
  Whenever a task, workflow, or custom setup is completed (e.g., media processing, IoT/Raspberry Pi management, Home Assistant automations, network configurations, password vault operations, or system utilities), **proactively suggest to the user to package it into an Antigravity Skill (`~/.agents/skills/<name>`), CLI shortcut (`~/.local/bin/`), or MCP server**.
- **Clipboard & Automation First:**
  Whenever presenting names, tokens, commands, or codes that the user needs to paste elsewhere, always auto-copy them to the system clipboard using `wl-copy` / `xclip` so the user only has to press `Ctrl + V`.
- **System Environment:**
  - OS: Zorin OS / Ubuntu (Linux, Wayland).
  - Terminal shortcuts live in `~/.local/bin/`.
  - Skills live in `~/.agents/skills/`.
  - Auto-Pilot Skill: `~/.agents/skills/auto-pilot/`
  - Rollback Safety Utility: `agent-undo` (`undo`, `undo-commit`, `hard-reset`, `audit`)

## Model Selection Preferences (CRITICAL)
- **Coding tasks** (writing, reviewing, refactoring, or auditing code): Always use **Claude Sonnet 4.6 (Thinking)** — this includes any task involving `media_server.py`, tests, CI/CD, scripts, or architectural changes.
- **General / everyday use** (research, web lookups, answering questions, Home Assistant queries, clipboard operations, quick commands): Use **Gemini Flash** (the default fast model).
- When in doubt: if the task involves editing or generating source code files, default to Claude Sonnet 4.6 (Thinking).

## Mandatory Production & Language Guidelines (media-studio-hub)
- **ENGLISH ONLY FOR ALL PRODUCTION ASSETS (CRITICAL):**
  All code, commit messages, git tags, GitHub releases, docstrings, markdown documentation, PR descriptions, and repository assets MUST ALWAYS be written in English. Spanish/Spanglish is strictly for direct chat with the user.
- **ITERATION-BASED VERSION COMMITS:**
  Whenever completing a phase or iteration cycle (e.g., security, refactoring, or UI integration), you MUST package those changes into a clean Git commit representing that milestone (e.g., `git add .` and `git commit -m "chore(iteration): complete [phase] milestone"`).
- **VISUAL & UI VALIDATION (PLAYWRIGHT / E2E):**
  Always run Playwright E2E tests after any frontend, template, or styling modifications to guarantee no interface elements or components are broken or inconsistent. If new UI views/components are added, expand the Playwright suite to cover them.
- **WORKFLOW PHASES:**
  1. Iteration 1: Security & Backend Hardening.
  2. Iteration 2: Refactoring & Code Cleanup.
  3. Iteration 3: UI, Styling & Playwright E2E Tests.
- **FINAL RELEASE (COMPACTED RELEASE & SEMANTIC GIT TAG):**
  Upon completing the full 3-iteration cycle with 100% green tests:
  1. Create a semantic Git tag (e.g., `git tag -a v1.0.0 -m "Release v1.0.0: Production-ready build with backend hardening, modular templates, and Playwright E2E suite"`).
  2. Push the tag to origin (`git push origin --tags`).
  3. Publish the GitHub Release with a consolidated English changelog covering all 3 phases (via GitHub REST API or CLI).

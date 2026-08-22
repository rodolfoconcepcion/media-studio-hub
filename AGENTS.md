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

## Model Selection Preferences (CRITICAL)
- **Coding tasks** (writing, reviewing, refactoring, or auditing code): Always use **Claude Sonnet 4.6 (Thinking)** — this includes any task involving `media_server.py`, tests, CI/CD, scripts, or architectural changes.
- **General / everyday use** (research, web lookups, answering questions, Home Assistant queries, clipboard operations, quick commands): Use **Gemini Flash** (the default fast model).
- When in doubt: if the task involves editing or generating source code files, default to Claude Sonnet 4.6 (Thinking).

## Reglas Obligatorias de Desarrollo & Calidad (media-studio-hub)
- **REGLA DE VERSIONES POR ITERACIÓN:**
  Cada vez que completes un ciclo completo o iteración de mejora (por ejemplo, terminar seguridad, refactorización o la integración de UI), DEBES empaquetar esos cambios en un commit limpio de Git que represente esa versión (ej: `git add .` y `git commit -m "chore: version completada de [fase]"`).
- **REGLA DE VALIDACIÓN VISUAL Y DE UI (PLAYWRIGHT / E2E):**
  Si el proyecto cuenta con pruebas de Playwright o tests E2E, EJÉCUTALAS después de cada cambio de frontend o estilos para asegurarte de que no rompiste la interfaz ni dejaste componentes inconsistentes. Si no existen pruebas de UI y hay cambios visuales, crea un test básico de Playwright para validar que la app carga y los elementos clave responden.
- **FLUJO POR ITERACIONES:**
  1. Iteración 1: Seguridad y Backend.
  2. Iteración 2: Refactorización y Limpieza de Código.
  3. Iteración 3: UI, Estilos y Tests E2E con Playwright.
- **REGLA DE RELEASE FINAL (COMPACTED RELEASE & GIT TAG):**
  Al terminar el ciclo completo (las 3 iteraciones pasadas y validadas con tests E2E verdes), DEBES:
  1. Crear un Git Tag semántico con la nueva versión (ej: `git tag -a v1.0.0 -m "Release v1.0.0: Producción lista con seguridad, refactorización y UI E2E"`).
  2. Empujar el tag al repositorio (`git push origin --tags`).
  3. Crear el Release en GitHub con el resumen consolidado de las 3 fases (vía GitHub CLI `gh release create` o GitHub MCP).

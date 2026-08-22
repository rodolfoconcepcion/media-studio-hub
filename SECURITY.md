# Security Policy

## Supported Versions

Security updates and vulnerability patches are actively maintained for the following versions:

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |
| < 1.0.0 | :x:                |

---

## Reporting a Vulnerability

We take the security of **Media Studio Hub** very seriously. If you discover a potential security vulnerability, please do **NOT** open a public issue.

### How to Report

1. **GitHub Private Vulnerability Reporting (Preferred)**:
   Navigate to the [Security Tab](https://github.com/rodolfoconcepcion/media-studio-hub/security/advisories) of this repository and click **Report a vulnerability**.
2. **Direct Contact**:
   Alternatively, email the maintainer at `concepcion.fam@gmail.com` with the subject `[SECURITY] Media Studio Hub Vulnerability`.

### What to Include in Your Report

- A clear description of the vulnerability and its potential impact.
- Step-by-step reproduction instructions or a minimal Proof of Concept (PoC).
- Any relevant logs, request payloads, or system details.

### Response Timeline

- **Initial Response**: Within 24-48 hours acknowledging receipt.
- **Assessment & Fix**: Vulnerability triage and patch development within 5-7 business days.
- **Coordinated Disclosure**: A security advisory and release patch will be published jointly once resolved.

---

## Built-in Security Safeguards

Media Studio Hub implements multiple layers of backend hardening:
- **Path Traversal Protection (`_safe_path`)**: All file operations are strictly sandboxed within authorized directories.
- **Least-Privilege Subprocess Execution**: Strict process-group management (`killpg`) to eliminate orphaned child tasks.
- **Input Sanitization**: Whitelisted character filtering on file paths, playlist names, and tag metadata.

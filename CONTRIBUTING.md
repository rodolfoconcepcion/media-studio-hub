# Contributing to Media Studio Hub

Thank you for your interest in contributing to **Media Studio Hub**! 🎉

We welcome contributions of all kinds: bug fixes, new features, documentation improvements, Home Assistant integrations, and translations.

---

## 🛠️ Development Setup

1. **Fork and Clone**:
   ```bash
   git clone https://github.com/rodolfoconcepcion/media-studio-hub.git
   cd media-studio-hub
   ```

2. **Create a Virtual Environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements-dev.txt
   ```

3. **Run the Local Server**:
   ```bash
   python3 media_server.py
   # Visit http://localhost:8888
   ```

---

## 🧪 Testing Guidelines

Before opening a pull request, ensure all tests pass:

```bash
# Run unit & integration tests
python3 -m unittest discover -s tests

# Run Playwright E2E UI tests
python3 tests/test_ui_e2e.py
```

---

## 🌿 Git Branch & Commit Conventions

- Use semantic commit prefixes:
  - `feat:` New features or UI components
  - `fix:` Bug fixes or security patches
  - `docs:` Documentation updates
  - `chore:` Dependency or workflow updates
  - `test:` Adding or improving test cases
- Keep all commit messages, comments, and documentation in **English**.

---

## 📬 Submitting a Pull Request

1. Create a feature branch: `git checkout -b feat/my-new-feature`
2. Commit your changes with clear messages.
3. Push to your fork: `git push origin feat/my-new-feature`
4. Open a Pull Request against the `main` branch.

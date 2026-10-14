# Contributing

Use Python 3.12+ and Node 22.12+. Install locked dependencies, install the project, and run backend tests plus frontend tests/build as described in README.

Keep changes focused and describe the user-visible behavior, implementation reason and validation. Use synthetic cases. Preserve existing frozen data and historical scores; create a new version for changed protocols or references.

New agent behavior should include typed outputs, a budget, traceable failures and regression coverage. Output changes must pass the existing review/citation/approval chain. Preserve permissions and case-state idempotency when extending API operations.

Before submitting, run `python scripts/release.py audit --root .` and `python scripts/verify_archive.py`. Do not commit API credentials, databases, caches, virtual environments, generated runs or third-party assets without a recorded license.

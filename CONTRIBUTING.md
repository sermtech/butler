# Contributing

Butler is a personal project that grew up in public. If you share the vision,
you are welcome to join in.

## Where to start

- **Ideas, questions and show-and-tell:** open a thread in
  [Discussions](https://github.com/owner-org/butler/discussions).
- **Bugs:** open an issue with what you did, what happened, your OS and the
  commit you ran. `python scripts/install.py --check` output helps.
- **Security problems:** never in public. See `SECURITY.md`.

## Making a change

1. Read `AGENTS.md` first. It is the contract for this repository (for people
   and coding agents alike), and each folder that has its own `AGENTS.md`
   adds local rules. Specs live in `docs/`, acceptance criteria in `plan.md`.
2. Tests first: write a test that fails for the reason you expect, then make
   it pass. Run the tests for what you touched (see "Tests" in `README.md`).
3. Keep it small. One change per pull request, with the "why" in the
   description and the tests you ran, with their results.
4. No fallbacks that hide errors, no Unicode in code or console output
   (Windows consoles break on it), and update the docs a change makes stale.

By contributing you agree that your contribution is licensed under the
Apache License 2.0, the same as the project.

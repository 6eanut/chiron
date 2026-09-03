# Contributing

Thanks for your interest in CHIRON. This is the implementation companion to an
academic paper, so we keep the contribution bar low and the code review high.

## Setup

```bash
pip install -e .[dev]
```

This installs the `chiron` console entry point and the dev tools (ruff, pytest).
No external secrets are required to get started.

## Smoke checks you can run

- `chiron collect-demo` - exercises the offline knowledge and diagnosis
  plumbing without an API key.
- `chiron build-kb --config config.yaml` - builds the offline knowledge base.
- `chiron run --config config.yaml --artifact crash.json` - end-to-end repair
  (requires `DEEPSEEK_API_KEY`).
- `python -c "import chiron; import chiron.cli"` - import smoke check.

Start from `examples/config.example.yaml` for a working configuration.

## Tests

Automated tests are being validated and will be wired into the repository, the
local workflow, and CI in a follow-up. Until then, run the smoke checks above
to confirm nothing is broken, and note in your pull request how you validated
the change.

## Review expectations

- Keep functions small and focused, files cohesive, and no deep nesting.
- Prefer immutable patterns: return new values rather than mutating in place.
- Validate input at system boundaries and handle errors explicitly.
- Never commit secrets. The LLM key is read from the environment only
  (`DEEPSEEK_API_KEY`); it does not belong in any file.
- Open a pull request and a reviewer will engage within a few days.
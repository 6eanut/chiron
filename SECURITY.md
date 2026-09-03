# Security Policy

## Reporting a vulnerability

Please report security issues privately rather than opening a public issue.

Email the maintainers at the contact address listed on the latest paper
submission, or open a private advisory on the repository (GitHub
"Security" tab, then "Report a vulnerability"). You should receive an
acknowledgement within a few business days.

## Scope

This repository is the implementation companion to a research paper. Security
issues that matter here include prompt-injection risks in the LLM pipeline,
secrets leakage, unsafe handling of untrusted crash artifacts, and any
subprocess or file-path handling that could be abused.

## Secrets

CHIRON has no committed secrets. The LLM provider key is read from the
environment only, via the variable named by `llm.api_key_env` (default
`DEEPSEEK_API_KEY`). Never commit a key, token, or credential to this
repository. If you believe a secret has been exposed, rotate it immediately
and report it through the channels above.
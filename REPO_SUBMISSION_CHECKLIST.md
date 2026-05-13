# Repository Submission Checklist

Use this checklist before submitting this repository to external review platforms.

## Ownership / IP

- [ ] I am the owner of this repository and have rights to assign it.
- [ ] The code is personal/independent work or collaboration with rights cleared.
- [ ] No code from a current or former employer is included.
- [ ] No proprietary/internal/private source code is included.
- [ ] No third-party code is copied in a way that conflicts with this repo's license.

## Security / Privacy

- [ ] `.env` is not tracked.
- [ ] No webhook URLs, API keys, tokens, private keys, or credentials are committed.
- [ ] No personal data or confidential data is embedded in source, tests, or fixtures.

## Repository Quality

- [ ] README clearly explains purpose, architecture, setup, and testing.
- [ ] Tests pass locally (`pytest -q`).
- [ ] Project is meaningful and not tutorial scaffolding/homework/hackathon-only code.
- [ ] Commit history and docs reflect substantive engineering work.

## Final Verify Commands

```bash
git status --short
pytest -q
```

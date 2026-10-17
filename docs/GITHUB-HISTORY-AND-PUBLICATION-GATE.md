# GitHub history and public release gate (2026-10-17)

## Existing repository
- `statticus19/VinylPi` is currently **private** on `main`.
- GitHub's commits listing returned **4 commits** (a76b25a27a, 2b99da93f1, 4a4fdc24f5, 77defed47d).
- Recursive tree metadata was reviewed for all four. No filename that obviously contains a real credential was found; `firmware/CrowPanel_Remote_BMP/secrets.example.h` is an intentional example file.
- **This does not constitute a content or history secrets audit.** Commits can have hardcoded secrets in ordinary source files. Before changing repo visibility, scan the complete Git history, not just the tip, and rotate any compromised tokens.

## Publication gate
1. Review all repository history and release contents for secret values, internal addresses, unique hostnames, personal identities, and embedded credentials; use a scanner with history support.
2. Verify public-safe filenames and generated archives and review the scanner findings manually; zero findings is not absolute safety.
3. Verify rights for the 12 dashboard graphics and for firmware driver/library redistribution. Keep third-party Pokémon/UK imagery out of the repository.
4. Keep NAS code and any NAS examples/private backup entirely separate from the public tree and GitHub history.
5. Validate generic service unit installation, dependencies, and fresh-install documentation offline. No live installation test is planned by user request; disclose limitations in release notes.
6. Do not overwrite `main`, change visibility, or push a branch until the branch contents are confirmed suitable for GitHub's servers. A *private* GitHub branch is still a remote copy of the source.

## Warnings versus blockers
`YOUR-VINYLPI-HOST` and `YOUR-PI-LAN-IP` placeholders must be configured by the end user. Their presence in templates/docs is expected, not evidence of secret exposure. Their presence in live application code may instead indicate an unresolved runtime configuration gap; review case by case rather than globally suppressing warnings.

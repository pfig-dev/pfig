# Security Policy

## Trust boundary

pfig is a configuration and execution tool, not a sandbox. Treat these as **trusted code or trusted artifacts**:

- `.pfig` configuration files and anything they `import`
- Portable bundles created with `pfig bundle`
- Snapshots and replay workspaces produced by `pfig replay`

Running pfig may execute Python callables, modify the filesystem, run `git` commands, and install packages from snapshot metadata. Do not run configs, bundles, or snapshots from untrusted sources.

## Sharing artifacts

Snapshots are not redacted. They can contain runtime external values, command-line
arguments, Git remote URLs, raw configs, dependency locks, and source patches.
Inspect them before sharing, and do not publish snapshots that contain credentials
or private project information.

The graph viewer is intended for local use. Keep it bound to loopback: it does not
authenticate requests and can expose config values and source locations.

## Supported versions

pfig is in alpha (0.x). Security fixes are applied to the latest release only.

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, email **security@pfig.dev** with:

- A description of the vulnerability and its potential impact.
- Steps to reproduce, ideally with a minimal `.pfig` snippet.
- The pfig version affected.

We will acknowledge receipt within 5 business days and aim to provide a remediation plan within 30 days. Once a fix is available, we will coordinate a disclosure timeline with you and credit you in the release notes (unless you prefer to remain anonymous).

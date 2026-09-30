# Security policy

## Supported versions

The default branch is supported. Other versions do not receive security
maintenance.

## Scope

This library reads SREF recipe JSON, packages, and bundles that may come from an
untrusted source. It treats every byte of them as hostile. A report is in scope
when a crafted artifact makes the reader do any of the following:

- raise anything other than its documented errors, such as an uncaught
  `RecursionError`, `MemoryError`, or `zipfile` exception;
- write, or name a path, outside the archive it is reading;
- use more memory, time, or disk than its limits allow, including through
  nesting, compression ratio, entry counts, or a bundle of packages;
- accept an artifact the specification requires it to reject, or read a
  different meaning from a document than the specification defines; or
- run code, open a network connection, or read a file it was not given.

The reader enforces the archive rules of the specification, strict JSON, digest
checks, and the finite limits in [`limits.py`](sref_reader/limits.py). It
reports an exceeded limit under `resource-limit`. The values of those limits are
a policy the calling application chooses, so a limit set too high for an
application's memory is a configuration matter and not a vulnerability.

Out of scope: how an application renders, stores, or forwards the text and media
it reads, and the security of the specification itself, for which see the SREF
repository's policy.

## Reporting

Use the hosting platform's private vulnerability-reporting feature when it is
available. Otherwise contact a maintainer privately through the hosting platform
and ask for a secure reporting channel. Do not include exploit details in a
public issue.

A report should include the affected artifact or a way to build it, the version,
and the effect. A fix adds a regression test that reproduces the issue without
unnecessary risk.

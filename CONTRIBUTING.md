# Contributing

## Report a defect

Open an issue at <https://github.com/savorum/sref-reader/issues>. Give the library
version, the Python version, the input or a way to build it, what you expected, and what happened.
Report a vulnerability privately, as [`SECURITY.md`](SECURITY.md) describes.

## Make a change

[`docs/development.md`](docs/development.md) describes the checks and the
conformance run. A change to behavior needs a test, and a fix for a hostile
input needs a regression test. What the specification requires is settled in the
[SREF repository](https://github.com/savorum/sref); this library is tested
against its corpus.

## Sign off commits

Every commit carries a Developer Certificate of Origin sign-off, created with
`git commit -s`:

```text
Signed-off-by: Your Name <your-email@example.com>
```

The sign-off certifies the statement at <https://developercertificate.org/>, and
CI rejects a commit whose sign-off does not name its author.

## Conduct

This project follows the
[SREF code of conduct](https://github.com/savorum/sref/blob/main/CODE_OF_CONDUCT.md).

# Security assessment

This assessment covers the `sref_reader` library. It reads bytes from a caller
and returns values, so its exposure is the input it is given: every byte of a
recipe, package, or bundle comes from an untrusted source.
[`SECURITY.md`](../SECURITY.md) defines what counts as a vulnerability and how to
report one.

## Method

Each way hostile input could harm the process running the reader was matched to
a control in the code and to a test that fails without it.

## Findings

| Risk                                                   | Control                                                                                                                 | Evidence                                                                                                                                                                                   |
| ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| An exception other than `SrefError` escapes            | Every failure is reported as a violation. Nesting is measured before anything recurses. ZIP decoding failures are mapped. | [`tests/test_fuzz.py`](../tests/test_fuzz.py), [`fuzz/fuzz_reader.py`](../fuzz/fuzz_reader.py), and the `NestingDepth`, `MistypedMembers`, and `CorruptedEntries` cases in `tests/test_reader.py` |
| Path traversal, links, collisions, undeclared files    | The archive rules of SREF section 15 run before any content is read.                                                    | [`archive.py`](../sref_reader/archive.py) and the SREF conformance corpus, run by `tools/conformance.py`                                                                                    |
| Memory, time, or disk exhaustion                       | Finite limits in [`limits.py`](../sref_reader/limits.py), spent from one budget per operation, bundle members included. | The `ResourcePolicy` cases in `tests/test_reader.py`                                                                                                                                       |
| Declared sizes or digests that misstate the bytes      | Digests and sizes are computed from the bytes actually read.                                                            | `read_entry` in `archive.py` and the corpus                                                                                                                                                |
| Altered schemas or unit registry                       | The vendored snapshot is verified against its manifest digests on load.                                                 | [`snapshot.py`](../sref_reader/snapshot.py) and the `PinnedSnapshot` cases                                                                                                                  |
| Code execution, network access, or reading other files | The library takes bytes and returns values. It opens no sockets, files, or subprocesses and evaluates no code.          | A search of `sref_reader` for those calls finds none                                                                                                                                       |
| A malicious dependency                                 | One runtime dependency, `jsonschema`. CI installs by hash, and Dependabot proposes updates.                              | `requirements-ci.txt`, `.github/dependabot.yml`                                                                                                                                            |

## Continuous checks

CodeQL analyzes the code, Hypothesis property tests run with the unit tests, and
a coverage-guided fuzz target runs on pull requests and weekly.

## Residual risk

The limits are a policy the calling application sets, so defaults that are too
high for an application's memory are its configuration. The library does not scan
asset bytes for malware or check that they are the media type they claim.

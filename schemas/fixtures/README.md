# Fixtures — synthetic, never publishable

Everything under `schemas/fixtures/` is synthetic test data: a minimal,
internally-consistent publication cycle used by `validate.py --self-test`
and as a worked example of every contract. None of it is evidence.

`controllers.json` in particular carries only invented registry entries.
Every fixture controller's `controller_id` matches `^test_`, every name
contains the literal string `(fixture)`, and every `evidence_refs` URL is
under the RFC 2606 reserved TLD `https://fixture.invalid/` — a domain that
can never resolve and can never be mistaken for a live citation.

`validate.py`'s `check_no_fixture_artifacts` (doc 07 F-7 rule 9) hard-fails
any of these three markers — a `test_`-prefixed id, a `(fixture)` name, or
a `.invalid` URL — found *outside* `schemas/fixtures/`. That is the
publish-path gate: it is exempt here, by design, because this directory is
expected to contain exactly these markers, but a fixture-shaped value
anywhere in a real cycle directory does not publish. A hallucinated
reference has nowhere to live.

`fixtures/must-reject/` holds one case directory per rule this gate (and
the other doc 07 F-2/F-7 checks) is meant to catch — each a full bundle
that must fail `validate.py`, isolating one violation per case wherever
practical.

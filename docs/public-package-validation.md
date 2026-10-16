# Public packaging review handoff

Validation date: 2026-10-15. Base: public `main` at `163d97f`.

## Delivered boundary

- Installable public app with bundled UI and an explicit writable workspace/data
  configuration. Existing source launcher and pages remain available.
- One public implementation of feedback keys, grounded answers, retrieval tools,
  and agentic helpers, with compatibility aliases. Their implementation bodies
  are byte-identical to the base revision.
- Wheel/source artifact completeness and inclusion checks, versioned implementation
  identity, installed-app smoke tests, and expanded Python CI coverage.
- Public/private integration and rollback instructions in [public-package.md](public-package.md).

No private application was integrated or edited. No corpus/database migration,
hosted deployment, model change, provider-call behavior change, or ranking change
is part of this work. The latest public Library, Search A/B, catalog proxy, and
browser-boundary features were retained by starting from refreshed public main.

## Verification

| Check | Result and boundary |
| --- | --- |
| Unmodified baseline | 434 Python tests passed |
| Updated Python suite | 480 tests and 32 subtests passed in a fresh environment, including path/import isolation, legacy history recovery, schema resources, archive mutation checks, and provenance |
| Browser regressions | 31 passed; fixtures/static hosting verify UI behavior, not ingestion |
| Frontend build | Passed; checked-in bundle reproduced without changes |
| Public wheel/source audit | Passed; unknown/missing/duplicate files, divergent embedded manifests, and missing expected metadata are rejected |
| Fresh dependency installation | Passed; `pip check` found no broken requirements |
| Installed wheel | All 45 packaged Python modules imported; real server served all app pages and authenticated synthetic search from outside the checkout |
| Rebuilt source archive | Independently rebuilt, audited, reinstalled; installed smoke passed again |
| Persistence/restore | Synthetic store reloaded without checksum changes; copied snapshot restored and returned the same evidence |
| Real YouTube ingestion | Actual backend POST succeeded, job completed, video listed with 24 chunks; explicit cached multilingual E5 model produced 1,024-dimensional vectors |
| Bounded real-model parity | Base/new matched on three questions across dense, lexical, hybrid, plus agentic search and one raw-context read; identities/text/traces exact, scores compared at relative tolerance 1e-5 and absolute tolerance 1e-6 |
| Existing unfinished work | Eleven modified/untracked original files verified unchanged; original branch retained |

Real ingestion used the public 3Blue1Brown video `aircAruvnKk` in a new disposable
store. Its transcript, vectors, and comparison responses remain outside Git. An
initial offline lookup by model name fell back to hashing; that run was not used
as real-model evidence. The successful real-model run used the existing cached
snapshot explicitly and verified persisted embedding metadata.

This bounded parity check is migration evidence, not a general retrieval-quality
benchmark. Live answer-provider calls, live Codex generation, and OCR against a
real user-owned video were not run; their existing mocked regression coverage was
retained. Browser state keys/origins and UI content were unchanged.

## Review and adjustments

The implementation was reviewed locally, then sent as public code and public
test evidence to Muse Wonder 1.0 with High reasoning. No private source or runtime
data was sent. The first Muse review reported no proven P1 finding and identified:

| Finding | Disposition |
| --- | --- |
| CLI data path could disagree with import-time pipeline defaults | Fixed: dotenv and CLI overrides resolve once before application imports; real-import conflicting-dotenv test added |
| Distribution audit allowed missing required resources | Fixed: wheel and source archive must contain their complete expected sets; deletion mutation tests added |
| Source archive installation needed explicit validation | Added explicit tar-to-wheel rebuild, audit, reinstall, and repeat installed smoke in CI |
| Provenance omitted runtime resources | Fixed: the single packaged allowlist covers code, schemas, built UI, and evaluation resources |
| Legacy history might be orphaned | Existing current-then-legacy reader confirmed; seeded legacy-load/persist/restart test added without changing the original history |
| Generator schema might follow the writable workspace | Existing package-relative schema confirmed; unit and installed checks parse it outside the data workspace |
| Second review: provenance classification could diverge | Fixed: archive audit and provenance share one runtime/source-only classification |
| Second review: name-only audit missed duplicates, embedded-manifest mismatch, metadata | Fixed with canonical manifest comparison, duplicate rejection, exact distribution metadata/license requirements, and mutation tests |
| Second review: damaged current history hid valid legacy history | Fixed with fallback on unreadable/invalid current data, valid-current precedence, and a permission-restricted backup before any recovery write |
| Second review: secondary packaged modules were not imported | Added installed-environment import checks for every allowlisted Python module |
| Second review: final source-artifact verification was incomplete at review time | Closed by rebuilding the final source archive, auditing and reinstalling its wheel, then repeating the installed smoke |
| Final review: an empty allowlist could yield empty provenance | Fixed: empty input fails closed, with the literal empty-string regression exercised directly |

Muse's closure review confirmed no remaining P1/P2 in the bounded public-package
review. The preceding review found no other material design problem. Muse reviewed
provided public code and reported evidence; it did not independently run the suite.
The complete Python suite and both rebuilt installed-artifact checks passed again
after the final empty-manifest fix.

The next implementation milestone is private integration only after the user
reviews these public results. This handoff does not approve or start that work.

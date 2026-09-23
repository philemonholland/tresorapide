# Tresorapide online deployment implementation plan

Status: planned — not approved for production deployment yet  
Plan ID: `TR-DEPLOY-001`  
Prepared: 2026-09-13  
Continuity record: [`DIAGNOSTIC_LEDGER.md`](DIAGNOSTIC_LEDGER.md)

## Readiness decision

Tresorapide is **not ready to expose online today**. The repaired application is a
strong local release candidate, but the current runtime is explicitly a LAN/development
deployment rather than a production service.

Evidence already in hand:

- The complete application suite passes: 318 tests.
- `manage.py check --deploy` passes under synthetic production security settings.
- The Docker Compose file resolves successfully.
- The live local database and the five supplied paper BCs were reconciled against the
  supplied Grand Livre; the final derived reconciliation has no unexplained remainder.
- The current container still runs Django `runserver`, applies migrations, seeds BB data,
  and serves local static/media files every time it starts.
- The production candidate is not source-bound: roughly 2,885 added and 338 removed lines
  are still uncommitted on `main`.
- No production identity provider, managed secret store, durable object storage,
  infrastructure-as-code, recovery drill, monitoring, or supported release manifest exists.
- The 99% OCR target has not yet been demonstrated on an independent labelled corpus.
- `D:\.HORTRAME\git\website\tresorapide` is empty. Its parent is a static Netlify site,
  not a suitable host for Django/PostgreSQL. That website repository also contains unrelated
  uncommitted user work that must remain untouched.

These are release blockers, not objections to the application logic already repaired.

## Deployment boundary and recommended topology

The production service should be private and authenticated. Financial records, uploaded
documents, secrets, and the Django source must not be copied into the public website tree.

```text
HORTRAME website /tresorapide
        |
        | no-index launcher or 302 redirect only
        v
private tresorapide.<HORTRAME-domain>
        |
        v
Azure Container Apps: Django + Gunicorn
        |                 |                  |
        v                 v                  v
PostgreSQL Flexible   private Blob       Key Vault
Server                document store     via managed identity
        ^                 ^
        |                 |
        +---- durable OCR worker/job ----+---- approved OCR provider
                         |
                         v
               Monitor / Log Analytics
```

Recommended deployment:

- Azure Container Apps in Canada for immutable application and worker images.
- Azure Database for PostgreSQL Flexible Server with point-in-time restore.
- Private Azure Blob Storage for receipts, BCs, and Grand Livre uploads.
- Azure Key Vault references accessed through managed identity.
- Application Insights / Log Analytics with explicit document and secret redaction.
- A dedicated private subdomain for the app. The Netlify folder contains only a minimal
  launcher or redirect and `noindex` policy.

A full Netlify reverse proxy is not the default: Netlify documents a 26-second proxy timeout,
and OCR must not depend on one long browser request. A separate app origin plus an asynchronous
OCR job also gives cleaner authentication, retry, and cost controls.

## Non-negotiable release gates

Production goes live only when every gate below is green.

| Gate | Required evidence |
| --- | --- |
| Source | Clean reviewed commit, signed version tag, immutable image digest, SBOM, dependency and secret scan |
| Functional | Full suite green against PostgreSQL plus migration, rollback, browser, upload, and reconciliation tests |
| OCR quality | Independent golden corpus proves at least 99% exact field accuracy; critical identifiers and money fields cannot be silently accepted when inconsistent |
| Accounting | Grand Livre source values remain immutable; corrections are attributable append-only adjustments/reversals; accounting invariants balance exactly |
| Access | Private authentication with MFA/passkeys, house-level authorization, disabled production Basic Auth, rate limits, secure sessions |
| Privacy | Approved OCR data-processing path, private storage, encryption, retention/deletion policy, redacted logs |
| Recovery | Timed database and document restore drill passes; recovery-point and recovery-time objectives are recorded |
| Operations | Health probes, alerts, runbook, cost caps, backup monitoring, rollback, and named incident owner exist |
| HORTRAME | Module manifest, integrity registry, CRAS classification, database usage contract, lifecycle role, and allowlisted client release pass their validators |

For the 99% gate, the metric is exact agreement against human-labelled source documents,
not the model's self-reported confidence. Report results separately for document type and field.
BC number, invoice number, date, subtotal, taxes, total, person/apartment attribution, page
coverage, and receipt-to-BC grouping are all scored. Any arithmetic mismatch, missing page,
conflicting identifier, or ambiguous attribution forces review instead of automatic validation.

## Implementation sequence

### Phase 0 — Freeze and source-bind the repaired release

1. Reconcile every open item in `DIAGNOSTIC_LEDGER.md` against the current worktree.
2. Separate reusable application changes from one-time 2026 repair scripts and local evidence.
3. Create a release branch from the current verified commit; review and commit the intended
   application, migrations, tests, and documentation without staging local data or backups.
4. Record hashes for the source commit, migrations, requirements lock, build context, and
   validation corpus. Generate an SBOM and run dependency, container, and secret scans.
5. Tag the resulting release candidate. No later code change inherits its test result.

Acceptance: the release can be rebuilt from a clean clone and produces the same image digest;
the 318-test result is rerun against that exact source binding.

### Phase 1 — Register Tresorapide as a HORTRAME module

1. Complete the HORTRAME communication/session initialization before modifying HORTRAME.
2. Keep the reusable module in its own repository; classify its lifecycle role as `external`.
3. Add the required `AGENTS.md`, `README.md`, `SECURITY.md`, `hortrame.md` integrity registry,
   `DATABASE_USAGE.md`, module manifest, validation command, deployment documentation, and
   positive client-export allowlist.
4. Classify every stored or emitted record under HORTRAME-CRAS. Treat unknown records as
   consequential until reviewed.
5. Define a separate private deployment repository for domain, subscription, resource names,
   release approvals, and non-secret secret references. Do not place client data there.

Acceptance: the Common Core, integrity, module, database, CRAS, lifecycle, and export checks all
pass; an export built in an empty directory contains only allowlisted release files.

### Phase 2 — Make the application production-safe

1. Split development and production settings. Require production hosts/origins, HTTPS,
   trusted-proxy handling, secure cookies, HSTS, and explicit logging configuration.
2. Replace `runserver` with Gunicorn. Run as a non-root user from a pinned minimal base image;
   add startup, readiness, and liveness probes and graceful shutdown.
3. Remove automatic `seed_bb_data` from startup. Run migrations as a separately authorized,
   single-instance release step after backup; make seed/demo commands impossible in production.
4. Stop serving private media from Django. Use private Blob objects and short-lived authorized
   downloads. Serve static assets separately with versioned cache keys.
5. Remove production REST Basic Authentication. Integrate the selected identity provider with
   MFA/passkeys; test every view/API for house and role isolation.
6. Harden uploads with byte-signature/type/size/page limits, randomized storage keys, malware
   scanning/quarantine, and no executable inline rendering.
7. Add explicit Content Security Policy, frame, referrer, permissions, cache, and error-page
   behavior; prevent sensitive values or document text from reaching logs.

Acceptance: production settings fail closed when a secret, host, storage binding, HTTPS proxy,
or identity setting is missing; a non-root image passes security checks and production smoke tests.

### Phase 3 — Make OCR durable, measurable, and cost-bounded

1. Convert scan submission into an idempotent job keyed by the source-file hashes and extraction
   policy version. The web request returns promptly; a worker performs OCR.
2. Persist per-page coverage, model/prompt/schema version, retries, tokens/cost, validation
   failures, and source/result hashes. Never bill twice for an identical completed job.
3. Cap pages, file bytes, requests, retries, tokens, and daily/monthly spend. Stop the job on a
   deterministic preflight or validation failure instead of continuing an expensive bad batch.
4. Pin the OCR model/prompt/schema. A model change must beat the incumbent on the frozen golden
   corpus before release.
5. Keep deterministic arithmetic and cross-page checks outside the model. Require human review
   whenever critical evidence is missing or contradictory.
6. Build an independent labelled corpus containing clean, skewed, faint, handwritten,
   multi-page, duplicate, mixed receipt/BC, and unsupported samples. Split development and
   untouched acceptance sets.
7. Publish a reproducible evaluation report: exact field counts, critical-field failures,
   document-complete rate, false grouping/duplicate rate, confidence intervals, cost, and latency.

Acceptance: the independent acceptance set meets the 99% exact-field requirement and has zero
silently accepted arithmetic, page-coverage, grouping, and critical-identifier contradictions.

### Phase 4 — Enforce consequential-record auditability

1. Preserve original uploaded evidence with content hashes, storage version IDs, and a defined
   retention/immutability policy.
2. Replace any post-consequence update/delete path with attributed revision, correction, void,
   reversal, adjustment, archive, redaction, or disposition events.
3. Enforce this in PostgreSQL permissions, constraints, and triggers—not only Django code.
4. Make the accounting import authoritative and immutable. Keep each user override as the linked
   child adjustment already represented in the UI; archive only the active adjustment view.
5. Make business mutation plus audit/outbox entry one database transaction. Test denied direct
   database mutation using the actual production application role.

Acceptance: CRAS tests show ordinary production roles cannot overwrite, hard-delete, truncate,
or cascade-delete consequential records or their required evidence.

### Phase 5 — Provision isolated infrastructure

1. Create Bicep or Terraform for separate staging and production resource groups.
2. Provision Container Apps, private image registry, PostgreSQL, Blob Storage, Key Vault,
   monitoring, alerting, budgets, DNS/TLS, least-privilege identities, and network controls.
3. Enable database backups/PITR and the selected high-availability tier. Enable Blob versioning,
   soft deletion, retention, and immutability appropriate to the accounting policy.
4. Configure no plaintext secrets in Git, build logs, image layers, application settings, or
   screenshots. Rotate any secret ever used in a local/demo environment before migration.
5. Define rollback by prior image digest plus forward-compatible database rules. Destructive
   down-migrations are not a rollback strategy.

Acceptance: a clean staging environment is reproducibly provisioned; resource policy checks,
network tests, monitoring, backup jobs, and cost alerts pass.

### Phase 6 — Validate staging and recovery

1. Restore a sanitized copy of the current database and document structure into staging.
2. Run migrations, full PostgreSQL tests, OCR acceptance evaluation, reconciliation fixtures,
   authorization matrix, concurrency/idempotency tests, malicious-upload tests, and load tests.
3. Visually verify desktop and mobile workflows: login, scan, page preview, correction,
   validation, duplicate review, expense grid, GL import, adjustment, archive, and export.
4. Restore PostgreSQL and document storage from backup into a clean environment. Compare row
   counts, business totals, file counts/hashes, audit chains, and application behavior.
5. Exercise failed deployment, failed OCR, provider outage, worker restart, duplicate callback,
   and rollback scenarios.

Acceptance: the signed staging report binds every result to the release commit/image and contains
no production personal or financial data.

### Phase 7 — Migrate data and release privately

1. Produce a fresh local PostgreSQL dump and media manifest with hashes; verify both before freeze.
2. Enter a short write freeze, copy data through an encrypted path, restore into managed services,
   and rerun all counts, totals, hashes, active/reversed balances, and GL reconciliation controls.
3. Create named user accounts through the production identity provider; do not migrate reusable
   passwords from the LAN deployment.
4. Deploy the exact approved image digest with migrations performed once. Keep the previous
   release and the untouched migration backup available for rollback.
5. Start with a small internal pilot. Monitor errors, audit failures, OCR cost/accuracy, queue
   depth, database/storage health, and access anomalies before widening access.

Acceptance: authorized users can complete the critical workflows over HTTPS; unauthorized users
cannot discover or access records; recovery and rollback remain available throughout the pilot.

### Phase 8 — Add the HORTRAME website entry point

1. In the dirty website repository, stage only an explicit allowlist for
   `tresorapide/**` and the exact `netlify.toml` lines required.
2. Add a minimal bilingual launcher/status page or a 302 redirect to the private app subdomain.
   Store no application code, data, tokens, API keys, user names, or document metadata there.
3. Apply `X-Robots-Tag: noindex, nofollow, noarchive`, `Cache-Control: no-store`, and safe
   content headers. The protected app origin must emit its own security headers because Netlify
   does not apply static-site custom response headers to externally proxied content.
4. Run the website release verifier and inspect its full staged diff. Confirm every unrelated
   existing deletion/untracked file remains untouched.

Acceptance: the launcher reaches the healthy private app, reveals no private metadata, is not
indexable, and the website commit contains only the approved allowlist.

## Required choices before infrastructure work

These choices do not block source hardening or the OCR evaluation harness, but they do block
cloud provisioning:

1. Production hostname and whether the HORTRAME page should link or redirect to it.
2. Identity provider/tenant and the initial authorized users and roles.
3. Azure subscription, Canada region, backup retention, availability target, and monthly budget.
4. Required retention period for original BCs, receipts, Grand Livre files, and audit events.
5. Approval of the OCR provider/data-processing terms for private financial documents.

## Release handoff

The final handoff must include the version tag and image digest, migration and rollback IDs,
infrastructure version, inventory of deployed files/resources, OCR evaluation report, security
scan results, backup/restore evidence, reconciliation controls, monitoring links, remaining known
risks, and the exact website allowlist. “Online” is complete only after production smoke tests and
the first backup restore check, not when DNS first resolves.

## Primary references

- [Django deployment checklist](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/)
- [Django with Gunicorn](https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/gunicorn/)
- [Azure Container Apps architecture best practices](https://learn.microsoft.com/en-us/azure/well-architected/service-guides/azure-container-apps)
- [Azure Container Apps secure deployment](https://learn.microsoft.com/en-us/azure/container-apps/secure-deployment)
- [Azure Container Apps Key Vault secrets](https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets)
- [Azure Database for PostgreSQL reliability and high availability](https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/concepts-high-availability)
- [Azure Backup for PostgreSQL Flexible Server](https://learn.microsoft.com/en-us/azure/backup/backup-azure-database-postgresql-flex-overview)
- [Azure Blob immutable storage](https://learn.microsoft.com/en-us/azure/storage/blobs/immutable-storage-overview)
- [Netlify rewrites and proxy limits](https://docs.netlify.com/manage/routing/redirects/rewrites-proxies/)
- [Netlify custom-header limitations](https://docs.netlify.com/manage/routing/headers/)


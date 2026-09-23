# Tresorapide Diagnostic Ledger

## TR-OCR-001 — Multi-page paper BC extracts no fields

- Source: User report on 2026-09-12: a scanned `bon de commande` that previously would have worked now yields no detected fields.
- Status: verified
- Scope: Diagnosis completed; the user subsequently authorized implementation and deployment of the confirmed fix.
- Current evidence:
  - Running scan session `BonDeCommande.pk=113`, receipt `ReceiptFile.pk=140` (`BC17181.pdf`) is marked `EXTRACTED`, but all useful candidate fields are empty.
  - The same upload batch extracted useful fields from `BC17188.pdf` and `BC17190.pdf`, so API availability and the general parsing path are working.
  - `BC17181.pdf` has eight visually legible pages; page 1 visibly contains BC number `17181`, date, supplier/payee, two member names, line items, and total `347.36`.
  - The PDF's embedded OCR text also exposes `No 17181`, confirming that the source is not blank.
  - Tresorapide renders all eight pages into one 2200 x 24145 PNG and sends it as one `detail="high"` image.
  - OpenAI's current GPT-5.4 vision sizing rule limits a `detail="high"` image to a 2048-pixel maximum dimension and 2,500 patches. The 2200 x 24145 composite is therefore reduced to about 186 x 2048; each source page becomes only about 186 pixels wide.
  - The code declares `MAX_PAGES_PER_BATCH = 4`, but `_split_file_map()` places every PDF in one batch even when a single PDF has more than four pages. Runtime page counts were 8 for the failed PDF, 4 and 2 for the two successful PDFs in the same scan.
  - Focused batching tests all pass (6/6) but contain no case for a single PDF exceeding the four-page limit, so they preserve this gap.
- Acceptance check: Met. The source was visually verified as legible, the exact produced image was measured, the documented API resize was calculated, and successful shorter PDFs in the same request rule out a general key/model/parser outage.
- Evidence links: `bons/ocr_service.py`; `bons/tests.py`; runtime scan session 113; task-local PDF and render artifacts; official OpenAI Images and Vision documentation.
- Source binding: repository commit `40e4ce5f0126faa6a01931b15701392fe8887332`; `bons/ocr_service.py` SHA-256 `D985B23E5F19C141EA51D3406CDEC149A955C40D218E9617776A6FF27E95CAE1`; `BC17181.pdf` SHA-256 `DE7F0649253ABBC16CFEB7E30B88DC2600A5EFABAF69CF78FE24909263463051`.
- Diagnostic boundary: A proposed second OpenAI call on page 1 alone was not made because it would resend a private purchase-order page without separate authorization. The diagnosis does not depend on that call.
- Initial authorization interpretation (superseded): The 2026-09-12 request to fix and validate the uploaded BCs was initially read as authorizing implementation, deployment, reprocessing, and source-to-extraction validation.
- Revised boundary: Implementation, deployment, and local source inspection are authorized. The security gate requires a separate explicit acknowledgement before the private PDFs can be resent to the external OpenAI OCR service; reprocessing has therefore not occurred yet.
- Fix evidence: The live container now sends separately preserved page images with `detail="original"`; an eight-page PDF becomes ranges 1-4 and 5-8. The deployed source hash matches the tested workspace source (`22C567F10B526A46E6FBA8096C10C9289077E91401CCCBF43725EFE39EA5FB39`).

## TR-OCR-002 — Empty AI output is marked `EXTRACTED`

- Source: Found while tracing TR-OCR-001.
- Status: verified
- Meaning: `process_receipts_batch()` marks a receipt `EXTRACTED` whenever the API returns parseable JSON, even if every useful candidate field is empty. That hides the failed extraction behind a success status and prevents the normal finalize safety-net from retrying it.
- Acceptance check: Add a meaningful-field predicate, surface a truthful warning/status, and cover empty-but-valid JSON with a regression test.
- Evidence links: `bons/ocr_service.py`; runtime receipt 140.
- Verification: Empty-but-valid JSON now produces `FAILED`, a user-facing warning, retained raw audit JSON, and empty candidate fields. Covered by the focused test and the 250-test suite.

## TR-OCR-003 — `reimburse_to` is discarded by the parser

- Source: Found while tracing TR-OCR-001.
- Status: verified
- Meaning: The prompt requests `reimburse_to`, and persistence reads it, but `_parse_one()` omits the field, so that candidate is always lost even on otherwise successful paper-BC extraction.
- Acceptance check: Preserve and validate `reimburse_to` through parsing and persistence, with paper-BC tests for both `member` and `supplier`.
- Evidence links: `bons/ocr_service.py`; `bons/tests.py`.
- Verification: The parser now normalizes `member`/`supplier`, rejects unknown values, and persists the surviving value. Covered by focused parser tests and the 250-test suite.

## TR-OCR-004 — Validate all uploaded BC packages against their source pages

- Source: User follow-up on 2026-09-12: “Validate the fix with your own intelligence... Look at the BC I put and make sure they're all properly scanned”.
- Status: verified
- Scope: After the code fix is tested and deployed, reprocess scan session 113 and manually compare every extracted document against all pages of `BC17181.pdf`, `BC17188.pdf`, and `BC17190.pdf`.
- Acceptance check: Every source page is accounted for; BC number, date, supplier/payee, purchaser/validator names and apartments, amounts, and summary agree with the visible documents or are explicitly flagged for manual review where genuinely ambiguous.
- Evidence links: runtime scan session 113; task-local PDF/render artifacts; post-fix OCR records.
- Authorization: On 2026-09-12 the user answered “Yes” to the explicit request to resend the three private PDFs to OpenAI and update OCR records in scan session 113. The prior blocker is resolved.
- Pre-fix local audit baseline:
  - `BC17181.pdf` (8 pages): current OCR is unusable. The visible BC is `17181`, dated 2026-05-16, total `347.36`, with seven supporting purchase totals that reconcile exactly to `347.36`. All eight pages are legible and accounted for locally.
  - `BC17188.pdf` (4 pages): current OCR is materially wrong. It reports BC `17088`, date `2024-01-23`, total `52.16`, and only two documents. The visible source is BC `17188`, dated 2026-04-23, total `62.10`, followed by three distinct Canva invoices of `20.70` each (January, February, and March 2026).
  - `BC17190.pdf` (2 pages): current OCR is mostly correct and covers both pages, but the stored supplier address reads `1245 rue Kitchener`; the visible BC reads `1215 rue Kitchener`. The BC total `22.98` agrees with the CANAC receipt (`19.99 + 1.00 + 1.99`).
- Deployed preprocessing verification: all 14 pages are emitted separately at widths from 725 to 2200 pixels and heights from 2675 to 3000 pixels; no vertical composite remains.
- Post-fix OCR verification:
  - `BC17181.pdf`: 8/8 pages represented as 9 documents (page 6 contains both an invoice and its card receipt). The review values reconcile exactly: `302.11 + 15.11 + 30.14 + 0.00 = 347.36`.
  - `BC17188.pdf`: 4/4 pages represented as the paper BC plus three distinct Canva invoices. The review values reconcile exactly: `54.00 + 2.70 + 5.40 = 62.10`.
  - `BC17190.pdf`: 2/2 pages represented; merchant `CANAC` and address `1215 rue Kitchener` now match the source. The review values reconcile exactly: `19.99 + 1.00 + 1.99 = 22.98`.
- Acceptance check: Met. All pages are accounted for, all three records are `EXTRACTED`, header/payee/signer fields were visually reconciled, and every review total equals its component amounts and the paper BC.

## TR-OCR-005 — Review form overwrites member payee and mixes partial invoice amounts

- Source: Post-reprocessing source-to-form validation for TR-OCR-004.
- Status: verified
- Meaning: The new OCR records are accurate, but the review-form supplement logic replaces a member payee with one linked merchant and fills subtotal/taxes from an incomplete invoice subset even when that subset does not reconcile to the paper-BC total.
- Acceptance check: Met. The paper-BC payee name/address remains authoritative for member reimbursement; one unambiguous supporting merchant is used, multiple merchants remain blank rather than being invented; and deduplicated invoice/receipt amounts are copied only when they reconcile to the BC total.
- Evidence links: `bons/views.py`; runtime review forms for receipts 140-142; source PDFs.
- Verification: 67 focused tests and the final 257-test suite passed; Django system checks and migration drift checks passed; the rebuilt live service is healthy.
- Final source binding: `bons/ocr_service.py` SHA-256 `22C567F10B526A46E6FBA8096C10C9289077E91401CCCBF43725EFE39EA5FB39`; `bons/views.py` SHA-256 `F713679E48F3A03FC80AD3FA9C4A81032BE98F736DA452096ABFBC612C10C6C8`; `bons/tests.py` SHA-256 `36F32E4878E5DC5F989D51966E6516738B366E1903A7641BCA279516797E0133`. The deployed container hashes match.

## TR-OCR-006 — Reprocessed BCs do not appear in the expense grid

- Source: User screenshot/question on 2026-09-12: “Why don't I see them here?”
- Status: verified
- Meaning: OCR extraction updates receipt candidates inside a scan session; it does not create expense-ledger rows. Scan session 113 (`BB260031`) remains `READY_FOR_REVIEW` with `is_scan_session=True` and still owns receipts 140-142. The expense grid lists `Expense` records created only when a non-session bon is validated.
- Existing-row clarification: The visible `17190` row is the older validated bon 111 / expense 96 / receipt 139. It is not the newly reprocessed receipt 142. No finalized bon or expense yet exists for `17181` or `17188`.
- Acceptance check: Met. Database ownership, status, and expense relationships explain every visible/missing row without assuming data loss.
- Evidence links: `budget/views.py`; `bons/views.py`; runtime scan session 113; validated bon 111; expense 96.

## TR-OCR-007 — Receipt confidence table looks mostly empty

- Source: User screenshots on 2026-09-12 showing receipt confidence summaries with many dashes/NA values.
- Status: verified
- Meaning: The summary renders the paper-BC and invoice field sets for ordinary receipts, even though those fields are structurally inapplicable, while omitting the corrected member-name field entirely. This makes valid receipt extraction look mostly empty.
- Acceptance check: Render only fields applicable to the detected document type, include the resolved member name for receipts, and retain genuine missing values such as purchase date as visible review items.
- Evidence links: `bons/ai_confidence.py`; `templates/bons/_receipt_confidence_summaries.html`; screenshots supplied by the user.
- Verification: The deployed summary for receipt 124 now contains only receipt-relevant fields and includes resolved member `Carole Lacourse`; paper-BC-only fields are absent. The live service is healthy and the 258-test suite passed.

## TR-OCR-008 — Legacy IGA receipt extraction contains wrong and missing values

- Source: Same user screenshots and follow-up that these records were expected to be perfect.
- Status: verified
- Meaning: Receipts 124-126 were processed through the pre-fix pipeline and then confirmed on 2026-03-25. All three source photos visibly show handwritten `#103 (27/12/23)`. Receipt 125 was stored as `31.58 + 1.58 + 3.15 = 36.70`, but the source reads `31.57 + 1.38 + 2.75 = 35.70`. Receipt 126 was stored as `84.92 + 4.24 + 8.46 = 92.78`, but the source reads `84.92 + 2.40 + 4.78 = 92.10`.
- Existing-record clarification: Pending bon 102 (`BB260021`) contains the bad legacy fields and totals `298.43`. Validated bon 109 / expense 95 (`BB260028`) is the confirmed duplicate with the correct total `296.75`, member `Carole Lacourse` / apartment `103`, and corrected receipt amounts; only one supporting receipt there still lacks its per-receipt date.
- Acceptance check: Met for diagnosis. The source images, candidate/final fields, duplicate flag, and validated duplicate explain the discrepancy without attributing it to the current page-preserving pipeline.
- Evidence links: receipts 124-126; validated receipts 135-137; duplicate flag 17; bons 102 and 109; expense 95.

## TR-OCR-009 — Conditional OCR model upgrade

- Source: User instruction on 2026-09-12 to change the model if the configured model is too weak compared with the earlier setup.
- Status: superseded
- Meaning: Benchmark the corrected page-preserving pipeline against the visible IGA ground truth before changing global configuration. If GPT-5.4 still misses visible dates, member resolution, or exact amounts, move OCR to a stronger current vision-capable model.
- Acceptance check: Compare exact field accuracy under the corrected preprocessing, record cost/capability tradeoffs from official OpenAI documentation, and change the configured model only with evidence that the upgrade improves this workload.
- Evidence links: `config/settings.py`; `.env`; official OpenAI GPT-5.6 Sol and GPT-6 Astra model pages; IGA receipt sources.
- Decision: Future OCR is now configured for `gpt-5.6-sol` with `medium` reasoning. Official OpenAI documentation identifies it as the current GPT-5.6 flagship with image input and Chat Completions support; the account model-access check and a non-sensitive parameter-compatibility call succeeded.
- Blocker: A source-bound benchmark on the three private IGA JPEGs requires explicit authorization to resend those images to OpenAI. No validated accounting record will be overwritten by the benchmark.
- Supersession: The IGA records were later identified by the user as foreign/test-like data and explicitly ordered removed. Bons 102/109 were voided and expense 95 was reversed under TR-OCR-014, so resending those private images is no longer useful or authorized. The already-deployed `gpt-5.6-sol` configuration remains in place for future OCR.

## TR-OCR-010 — Minimum 99% OCR accuracy

- Source: User requirement on 2026-09-12: “Nothing less than 99% accuracy is acceptable”.
- Status: verified
- Meaning: Treat 99% as a measured release threshold for exact critical-field correctness, not an unevidenced model claim. Track coverage separately so abstentions cannot inflate accuracy.
- Acceptance check: On a representative labeled corpus, achieve at least 99% exact match for auto-accepted critical fields (document/BC number, purchase date, member/apartment, merchant/supplier, subtotal, taxes, extras, and total), require 100% arithmetic reconciliation for accepted monetary fields, and route disagreements/low-confidence cases to explicit manual review rather than silently storing them as correct. Report sample size, field coverage, per-field accuracy, and confidence bounds.
- Evidence links: forthcoming labeled OCR regression corpus and evaluation report; current IGA and BC source-bound audits.
- Current state: The deterministic display, amount-reconciliation, high-resolution page transport, and stronger model configuration are deployed, but a 99% claim is not yet evidenced. The three IGA images provide a labeled regression seed, not a statistically representative corpus.

## TR-OCR-011 — Resolved member is displayed with an unrelated low OCR score

- Source: User screenshots/question on 2026-09-12: “Why is Carole Lacourse at 1?”
- Status: verified
- Meaning: The displayed member name may have been resolved from apartment/member records after OCR, while the badge beside it still comes from the original unreadable handwritten-name candidate. The UI therefore presents one row as if the value and score had the same provenance.
- Acceptance check: Trace the stored candidate, final value, and directory resolution; display truthful provenance for derived values rather than attaching an unrelated AI confidence score; cover the distinction with regression tests.
- Evidence links: receipts 124-126; `bons/ai_confidence.py`; confidence-summary template; forthcoming trace and test evidence.
- Verification: Receipts 124-126 store `member_name_candidate="ILLISIBLE"`, apartment candidate/final `103`, final member `Carole Lacourse`, and the legacy score `1`. The deployed summary now shows `Répertoire` with an explicit non-AI tooltip for Carole rather than `1`. The behavior is covered by a regression test and the 264-test suite.

## TR-OCR-012 — IGA receipts are attached to an apparent BC with no visible paper BC

- Source: User screenshots/question on 2026-09-12: “Why aren't they grouped up with the proper bon de commande? … Where is the actual paper bon de commande that it was trying to track?”
- Status: verified
- Meaning: Determine whether `BB260021` is a real scanned paper BC, an application-generated grouping identifier, or a scan-session artifact, and identify the complete upload/finalization lineage for receipts 124-126.
- Acceptance check: Account for every document in the originating upload, identify whether a paper-BC image exists, explain the grouping rule and any duplicate/split, and distinguish application IDs from source-document numbers.
- Evidence links: bon/receipt database relationships, audit log, receipt file storage, `_finalize_bons()` grouping logic.
- Verification: Scan session 100 (`BB260019`) contained four images: paper BC 15432 plus receipt photos A/B/C. A reset POST archived all four at 2026-03-25 04:34:14 UTC with reason `Capture mobile abandonnée par tresorierBB`. Scan session 101 (`BB260020`) began 43 seconds later and contained only three new receipt photos; finalization therefore treated them as regular receipts, grouped them by apartment 103, generated application number `BB260021`, and never set `is_paper_bc`/`paper_bc_number`. The missing BC remains intact as archived receipt 120.
- Fix verification: A single paper BC and otherwise unassociated receipts are now kept together only when every supporting total is known and their exact combined total equals the paper BC. Ambiguous/non-reconciling batches remain separate. “Recommencer” now states that the photos will not enter the next BC, requires browser confirmation, and records an immutable manifest audit entry. Covered by focused tests and the 264-test suite.

## TR-OCR-013 — Pending IGA bon contains arithmetically inconsistent confirmed amounts

- Source: User screenshots/question on 2026-09-12: “Why are the numbers not adding up?”
- Status: verified
- Meaning: `BB260021` displays component totals that do not reconcile with its stored total, while a validated duplicate appears to contain corrected receipt amounts. Trace when the wrong values were stored and why the pending duplicate remains visible.
- Acceptance check: Reconcile source-photo values, receipt-level stored values, bon aggregates, duplicate state, and validation history; prevent this inconsistent pending record from being presented as a valid grouping without an explicit warning or resolution path. Do not mutate or void accounting records without explicit authorization.
- Evidence links: bons 102 and 109; receipts 124-126 and 135-137; duplicate flag 17; audit log; source images.
- Verification: Receipt 125 stores `31.58 + 1.58 + 3.15 = 36.31` against total `36.70`; receipt 126 stores `84.92 + 4.24 + 8.46 = 97.62` against total `92.78`. Bon 102 independently sums those fields to `263.44 + 13.17 + 26.27 = 302.88`, while its stored total is `298.43`, a 4.45 difference. The source documents read `168.95 + 35.70 + 92.10 = 296.75`, matching paper BC 15432 and validated duplicate bon 109.
- Fix verification: The deployed detail/validation pages enumerate both bad receipts and the aggregate discrepancy. The validation form is absent while a discrepancy exists, and the server independently rejects a crafted validation POST. Receipt review now rejects complete monetary components that do not add to the stated total. Mixed-tax grocery receipts are accepted when their printed components reconcile, without assuming the entire subtotal is taxable. Live read-only rendering against bon 102 confirmed the 302.88/298.43 aggregate banner, both receipt warnings, and blocked validation; 264 tests passed.
- Data boundary: The legacy records have not been rewritten or voided. Repairing bon 109/expense 95, attaching archived paper BC 15432, and resolving the historical payee/signer attribution requires explicit authorization because it changes a validated accounting record.

## TR-OCR-014 — Remove stray IGA records and enter supplied BC PDFs

- Source: User instruction on 2026-09-12: “Destroy those I don't know where they came from, maybe tests. These are the ones I need to enter”, with `D:/Downloads/BC17181.pdf`, `BC17188.pdf`, and `BC17190.pdf` attached.
- Status: verified
- Interpretation: “Those” refers to the stray IGA lineage discussed immediately before this instruction: pending bon 102 (`BB260021`) and validated duplicate bon 109 (`BB260028`) / expense 95. The supplied BC PDFs are the authoritative documents to enter. Preserve the audit trail by using the application’s supported void/cancellation semantics rather than deleting protected accounting rows directly.
- Acceptance check: Confirm exact targets and source hashes; remove the stray IGA records from active/pending financial views without leaving their validated amount in the budget; enter and validate paper BCs 17181, 17188, and 17190 once each; confirm all source pages/documents are attached, totals reconcile, and the expense grid contains the intended rows without duplicates.
- Evidence links: user-supplied PDFs; scan session 113; bons 102/109/111/112; expenses 95/96; forthcoming post-operation database and UI verification.
- Source binding:
  - `D:/Downloads/BC17181.pdf`: SHA-256 `de7f0649253abbc16cfeb7e30b88dc2600a5efabaf69cf78fe24909263463051`, 8 pages.
  - `D:/Downloads/BC17188.pdf`: SHA-256 `fd0fa51f4e969b3961c940d60ff11ff2958759c78e9354199ddadaf463460154`, 4 pages.
  - `D:/Downloads/BC17190.pdf`: SHA-256 `333b2df357fdfe812e18953135b1a30b081df491e26354e56705204a3dd10c42`, 2 pages.
  - These hashes exactly matched staged receipts 140-142; no new OCR call or external upload was made.
- Safety evidence: Pre-operation PostgreSQL snapshot `backups/pre_stray_cleanup_20260912.dump`, SHA-256 `d966506679c92df2f94a2071621147e0a2f096b26e0c8500df6bb3dff90f73db`. The first transaction attempt failed on an unsupported outer-join row lock and rolled back fully. The corrected transaction then committed atomically. Execution script: `scripts/enter_authoritative_bcs_20260913.py`, SHA-256 `fd0f8c61c4e91845b3932678cc30703d5293163f905c4683d687c7f7ca28f29e` before this ledger update.
- Cleanup result:
  - Bon 102 (`BB260021`) is `VOID`; all its receipt files are archived.
  - Bon 109 (`BB260028`) is `VOID`; all its receipt files are archived.
  - Expense 95 (`+296.75`) has reversal expense 97 (`-296.75`). Both are hidden from the normal ledger while retaining an auditable zero net effect.
  - Audit entries record both voids and their receipt/expense manifests.
- Entry result:
  - Bon 114 / expense 98: paper BC `17181`, validated, 2026-05-16, `302.11 + 15.11 + 30.14 = 347.36`, purchaser `206 / Carl-David Fortin`, approver `201 / Alexis Camille Roman`, `Réparations BB` trace 2, authoritative receipt 140.
  - Bon 115 / expense 99: paper BC `17188`, validated, 2026-04-23, `54.00 + 2.70 + 5.40 = 62.10`, purchaser `201 / Alexis Camille Roman`, approver `203 / Jessica Bergeron`, `Autre dépenses` trace 99, authoritative receipt 141.
  - Existing bon 111 / expense 96 remains the single validated paper BC `17190`, 2026-06-17, `19.99 + 1.00 + 1.99 = 22.98`, purchaser `201 / Alexis Camille Roman`, approver `202 / Marylin Lamarche`, `Produits ménager/entretien` trace 7. Its older individual images were archived and authoritative PDF receipt 142 attached.
- Verification: Each paper number 17181/17188/17190 has exactly one active bon; all three detail pages render `VALIDATED`, show the supplied PDF filename, and contain no amount or paper/support mismatch warning. The default expense ledger renders all three numbers and totals while omitting BB260021/BB260028. Scan session 113 is void with no remaining active receipts. Five immutable audit entries cover cleanup, source replacement, and new entry. The deployed service is healthy, Django system checks pass, no migration drift exists, and the full suite passes 265/265.

## TR-DUP-001 — Duplicate notice dominates the expense row

- Source: User screenshot/request on 2026-09-13: move the yellow duplicate notice into a smaller, discreet column to the left of the date.
- Status: verified
- Meaning: The current expense grid paints the entire row yellow and appends `⚠ DOUBLON` to the description. Replace that presentation with a compact status cell before Date while preserving accessibility and discoverability.
- Acceptance check: A duplicate row keeps normal row styling; a narrow leading column contains a small yellow duplicate indicator with an explanatory tooltip/accessible label; ordinary rows keep alignment; PDF/Excel exports and accounting values are unchanged.
- Evidence links: supplied screenshot; `templates/budget/expense_ledger.html`; related CSS/tests; forthcoming rendered verification.
- Verification: `templates/budget/year_detail.html` now places a 1.1-rem muted-yellow `!` badge in a 2.2-rem `Avis` column immediately before Date. The full-row yellow background and visible `⚠️ DOUBLON` suffix were removed. The badge retains tooltip and `aria-label` text. A rendered-template check confirmed the cell precedes Date, the badge is present, and neither old presentation remains. Covered by the focused UI regression and the 272-test suite.

## TR-DUP-002 — Duplicate decision must respect dates and invoice numbers

- Source: User instruction on 2026-09-13: “go check if the dates and invoice numbers match because if they don't, they're not doublons”.
- Status: verified
- Meaning: Trace the currently flagged Clarke Fils 36.78 $ pair to its source documents and compare printed dates and transaction/invoice identifiers. Add deterministic negative gates so conflicting dates or identifiers cannot be labelled duplicates merely because supplier, amount, or description are similar.
- Acceptance check: Record the source-bound decision for the current pair; enforce exact normalized dates and document identifiers when both are present; treat conflicts as non-duplicates without an AI call; keep missing/ambiguous identifiers out of automatic confirmation; cover matching, conflicting, and missing-field cases with tests.
- Evidence links: bons/expenses/receipts underlying BB260003 and BB260009; `bons/ocr_service.py`; duplicate presentation/query code; source images.
- Source-bound decision: Receipts 98, 101, and 102 are byte-identical (`SHA-256 0e5c76ffe07df8e23f6988fa547609f42f32d64e2ff2344c8a4645cb9d54f69a`). Direct visual inspection of the source shows printed date `10/30/2024`, printed transaction number `465365`, the same two line items, and total `36.78`. The three uploads are duplicates; this conclusion does not depend on their OCR member attribution.
- Fix verification: Exact source hashes confirm duplicates without an AI call unless stored dates conflict. A known stored-date conflict now exits before AI and creates no flag. Non-identical images require the comparison result to return both dates and both invoice/receipt/transaction numbers; a date or normalized-number conflict deterministically forces non-duplicate, while missing evidence cannot receive automatic confirmed status. Validation-time checks no longer treat a matching total alone as a duplicate; they require an exact source hash or an already confirmed evidence-gated flag. Matching, conflicting-date, conflicting-number, missing-identifier, exact-hash, and validation-path regressions pass within the 272-test suite.

## TR-DUP-003 — Remove the incorrect Carl-David Clarke records

- Source: User instruction on 2026-09-13: “the one with Carl-David is definitly not good and should be removed”.
- Status: verified
- Interpretation: The visible Carl-David row is validated bon 90 (`BB260009`) / expense 94. A second pending Carl-David copy exists as bon 88 (`BB260007`) without an expense. Both use byte-identical `TestRecu1.png` content, so remove both active Carl-David variants to prevent the pending copy from resurfacing; retain bon 84 (`BB260003`) as the remaining Clarke record.
- Acceptance check: Void bons 88 and 90, archive their receipts, reverse expense 94 to zero net effect, preserve an audit manifest and backup, and confirm neither Carl-David row remains visible or pending.
- Evidence links: bons 84/88/90; receipts 98/101/102; expenses 91/94; duplicate flags 14-16; forthcoming post-operation audit.
- Safety evidence: Pre-operation snapshot `backups/pre_carl_david_cleanup_20260913.dump`, SHA-256 `f317879dbe49283b6d71ac1bd5aaa6c7e0f53115a947d2cfd05a835eee4b1130`. Atomic execution script `scripts/remove_carl_david_duplicates_20260913.py`, SHA-256 `904b5b8578270b7d7504c556a8330238214cd8917f4d974c6a53f8cba87b115b`.
- Verification: Bon 88 (`BB260007`) and bon 90 (`BB260009`) are `VOID`; receipts 101/102 are archived. Expense 94 (`+36.78`) has reversal expense 100 (`-36.78`). Audit entries 6/7 preserve both file and expense manifests. The default live budget view omits BB260007/BB260009, no duplicate flags remain actionable, and retained Clarke bon 84 (`BB260003`) / expense 91 is unchanged.

## TR-CLEAN-001 — Remove all pre-2026 test financial data

- Source: User instruction on 2026-09-13: “Remove all the pre-2026 stuff, that was just for tests they're not good”.
- Status: verified
- Interpretation: Remove active Tresorapide financial records with a purchase/expense date before 2026-01-01. Scope includes bons, their linked receipt files, and ledger effects; it excludes member/residency history, configuration, audit entries, and already-void historical rows. Use void/archive/reversal semantics so the normal UI is clean while preserving recovery and accountability.
- Acceptance check: Enumerate and source-bind every active pre-2026 bon/expense; snapshot the database; void each target bon, archive active receipts, reverse each unreversed expense, and record audit manifests; verify no active/pending pre-2026 bon or visible pre-2026 expense remains and current 2026 BCs are unchanged.
- Evidence links: forthcoming target manifest, database snapshot, atomic cleanup script, audit entries, and post-operation view/database checks.
- Target manifest:
  - Bon 82 / expense 88 / receipt 97: `17186`, 2021-11-26, 54.54.
  - Bon 85 / expense 92 / receipt 99: `BB260004`, 2024-10-24, 14.65.
  - Bon 86 / expense 93 / receipt 100: `BB260005`, 2024-10-26, 79.30.
  - Bon 84 / expense 91 / receipt 98: `BB260003`, 2024-10-30, 36.78.
- Safety evidence: Pre-operation snapshot `backups/pre_pre2026_cleanup_20260913.dump`, SHA-256 `d1f7d43fb35c36c4abb2500163f9e30de1cf781bf9ce3cba6cd4db9c6a5700c9`. Atomic execution script `scripts/remove_pre2026_financial_records_20260913.py`, SHA-256 `3660af515a3732e5d878ea6e6aa1807dad8c45266b628578f1ecf117b2f14173`.
- Result: Bons 82/84/85/86 are `VOID`; receipts 97/98/99/100 are archived. Expenses 88/91/92/93 have equal negative reversals 101/104/102/103 respectively. Audit entries 8-11 preserve each source and accounting manifest.
- Verification: No non-void bon has a purchase date before 2026-01-01; the four targets have zero active receipts; the default running-balance service returns no visible pre-2026 expense; and the rendered live budget view contains none of the pre-2026 dates or bon numbers. Current paper BCs 17181, 17188, and 17190 remain validated with totals 347.36, 62.10, and 22.98. The service is healthy.

## TR-GL-001 — Voided/test expenses appear as “Dépenses absentes du GL”

- Source: User report/screenshots on 2026-09-13: archived test records still appear under `Dépenses absentes du Grand Livre`.
- Status: verified
- Meaning: Reconciliation is using expense rows that have been cancelled/voided instead of the same active-ledger population shown in the expense grid.
- Acceptance check: Reconciliation excludes cancellation rows and originals that have a reversal, excludes expenses linked to void bons, and refreshes prior results so no archived test expense remains in missing-GL counts or tables.
- Evidence links: screenshots; `budget/gl_reconciliation.py`; expense cancellation/bon status data; forthcoming tests and live result.
- Implemented: A shared active-expense query now excludes cancellation rows, originals with reversals, and expenses linked to void bons in matching, reconciliation totals, import deduplication, AI context, and the detail view. Regression coverage includes reversed and void-bon expenses. The deployed code is active, but upload 13's persisted reconciliation summary still requires the authorized rebuild in TR-GL-003.
- Live verification: After the authorized upload-13 rebuild, `missing_from_gl_count` is 0 and the rendered detail page has no `Dépenses absentes du Grand Livre` section. The archived IGA/Clarke/test rows are absent.

## TR-GL-002 — Grand Livre workbook columns and residual are misinterpreted

- Source: User asks what `Écart résiduel inexpliqué` means; screenshots show `Solde fin Grand Livre = 0.00`, implausible GL amounts, blank rows, and `Exacte` matches despite incompatible amounts.
- Status: verified
- Meaning: Trace the authoritative workbook’s actual sheet layout, formulas, dates, debits, credits, descriptions, account/house structure, and ending balance. Identify precisely why the current import produces zero/shifted values and a meaningless residual.
- Acceptance check: Parse the correct BB 13-51200 transaction rows and monetary column(s), reject headers/formulas/blank rows as entries, calculate an independently reproduced ending balance, and replace/remove the residual label unless every term has a defined accounting meaning.
- Evidence links: `591-grands-livres-maisons-au-9-septembre-2026-1.xlsx`; imported upload/result rows; parser/reconciliation code; forthcoming cell-level audit.
- Source audit: Workbook SHA-256 `76365e211d5a6c95213060c6b1697ac8bca149483f5af93ce59ff23ff477fe8f`; BB account block `Sheet1!A61:I77`. Columns G/H/I are Debit/Credit/Ending balance. Rows 62-76 are 15 transactions; row 77 is the total. Independent totals are Debit `4753.16`, Credit `0.00`, Ending balance `4753.16` through 2026-09-09.
- Root cause: The parser assumed an obsolete 11-column I/J/K layout, so it read intermittent running balances from column I as transaction debits, treated row 77's total as a transaction, and continued into blank/header rows. It therefore stored 19 entries, zero source total, and the artificial `-19632.59` residual.
- Implemented: Monetary columns are resolved from header labels for both 9- and 11-column exports; compact total rows terminate the account section; blank/header rows are rejected; entry identities are stable by `(upload,row_number)` across reprocessing; and the residual label is now `Écart restant après les éléments listés`. The real source parses locally to the exact 15-row/4753.16 controls. Live upload 13 awaits the authorized rebuild.
- Live verification: Upload 13 now contains rows 62-76 only, with 15 entries, debit 4753.16, credit 0.00 and ending balance 4753.16. The prior garbage rows 77/83/84/87 were removed from the derived entry set. The rebuilt anomaly list contains no residual line; the 3676.61 difference equals the eight accounting-only rows exactly.

## TR-GL-003 — Reconcile five source BCs, Tresorapide, and accounting truth

- Source: User requires a complete discrepancy audit; the accounting workbook is base truth unless explicitly overridden. The workbook should omit only the three latest BCs.
- Status: verified
- Expected relationship: BC 16739 and BC 17186 must already be reflected in the accounting workbook; BC 17181, BC 17188, and BC 17190 may be absent because they are newer than the workbook cutoff. No other active Tresorapide expense may be absent, and no unexplained accounting entry may remain.
- Acceptance check: Visually verify all pages of BC16739/17181/17186/17188/17190; compare dates, BC numbers, payees, descriptions and exact amounts against active Tresorapide and the workbook; repair confirmed Tresorapide/import errors; final reconciliation reports exactly the three expected post-cutoff missing BCs and no other discrepancy.
- Evidence links: five supplied PDFs; authoritative workbook; live database; forthcoming comparison matrix.
- Source comparison:
  - BC16739: source date 2026-01-07, invoice 4999081, total 19.49; Tresorapide bon 81/expense 87 agrees; workbook row 62 agrees.
  - BC17186: paper/invoice date 2026-02-26, invoice 513165, `47.44 + 2.37 + 4.73 = 54.54`; workbook row 65 agrees. Tresorapide's pre-fix OCR incorrectly stored 2021-11-26, which caused bon 82/expense 88 to be voided/reversed under TR-CLEAN-001.
  - BC17181: source total 347.36; Tresorapide bon 114/expense 98 agrees; workbook row 69 contains BC17181 and debit 347.36.
  - BC17188: source total 62.10; Tresorapide bon 115/expense 99 agrees; workbook row 74 contains BC17188 and debit 62.10.
  - BC17190: source total 22.98; Tresorapide bon 111/expense 96 agrees; workbook row 73 contains BC17190 and debit 22.98.
- Conflict requiring user authority: Contrary to the expected “three latest missing” state, the supplied accounting workbook itself contains BC17181, BC17188, and BC17190. Following the confirmed base-truth rule, the repair will not pretend those source rows are absent. Restoring BC17186 and removing its prior reversal changes the user's earlier cleanup outcome; the prepared atomic live rebuild was blocked pending a fresh explicit authorization.
- Prepared post-rebuild controls: 15 GL entries; source/adjusted GL 4753.16; active grid 1076.55 after BC17186 restoration; 7 exact matches; 8 accounting-only unmatched entries totalling 3676.61; 0 grid expenses absent from GL; 0 unexplained remainder; 0 AI calls.
- Authorization and execution: On 2026-09-13 the user explicitly authorized the live restoration/rebuild. Fresh snapshot `backups/pre_authorized_gl_rebuild_20260913.dump` has SHA-256 `55eb708ddb0a036d8a09ea5c637811eb11777f60b45944fac449d5b4688f4512`. Atomic script `scripts/rebuild_gl_reconciliation_20260913.py` has SHA-256 `439e0e5f2703c139904b672367981f8befe34ddab7e11adc5bd157c4e2790480`.
- Result: Bon 82 / expense 88 / receipt 97 were restored as validated BC17186 dated 2026-02-26; erroneous reversal 101 was removed; the receipt is active with two pages; expense 88 is matched and marked validated in the GL. Audit entry 12 records the source-date correction. Upload 13 was reprocessed without AI calls; audit entry 13 binds the source hash, old/new row IDs and final controls.
- Final comparison: Seven exact matches are rows 62/63/64/65/69/73/74 to expenses 87/89/90/88/98/96/99. The workbook contains all five supplied BCs, including the three described as expected missing; under the base-truth rule, the final correct missing-from-GL count is 0 rather than 3. The remaining eight rows are accounting entries absent from the Tresorapide grid, not an unexplained discrepancy.

## TR-GL-004 — Accounting override, linked adjustment, and archive lifecycle

- Source: User workflow specification on 2026-09-13.
- Status: verified
- Confirmed rules: Imported Grand Livre rows are immutable base truth. A user override never overwrites the accounting row. It is marked by a red icon in the left column and owns a distinct adjustment row rendered immediately underneath through a stable foreign-key/order relationship. The adjustment subtracts the disputed amount from accounting-derived totals. After confirmation from the central cooperative, the user can archive the override/adjustment so it no longer appears in the active table.
- Acceptance check: Add normalized override/adjustment state with foreign keys, exact decimal amount, reason, actor/timestamps and archive metadata; enforce one stable active child row directly below its source independent of sorting; display red override indicator; apply the adjustment exactly once to presented totals; archive without deleting source accounting data; audit create/edit/archive actions; cover reorder, zero/partial/full adjustment, double-count prevention and archive behavior.
- Evidence links: forthcoming models/migration, views/forms/templates, accounting-total service and tests.
- Verification: Migrations 0009/0010 add `GrandLivreAdjustment`, nonnegative exact-decimal amounts, one active adjustment per source row, stable `(upload,row_number)` identity, actor/timestamps/archive fields, and source/adjusted reconciliation totals. The source row remains immutable; the active adjustment appears as a red-icon child row directly beneath it, subtracts exactly once, disables zero-effective imports, and can be archived with an audit entry. Reprocessing preserves the source-row/adjustment FK. Zero, partial, full, excessive, create, render, stable-link and archive cases pass. Deployed migrations and system checks are healthy.

## TR-GL-005 — Final table must use correctly parsed accounting values

- Source: User instruction: “make sure the values in the grand livre are correctly reflected in the final table”.
- Status: verified
- Meaning: Matched/unmatched/final tables must show the source transaction amount and date from the correct workbook cells, not a balance column, blank formula result or Tresorapide amount relabelled as GL.
- Acceptance check: Every displayed GL value traces to the imported source row/cell and sign convention; matched rows separately show GL amount and grid amount; summaries use accounting totals plus active adjustments exactly once; source zeros remain zero and missing values remain unavailable rather than fabricated.
- Evidence links: workbook cell map, imported entry models, detail template, calculation tests and live render.
- Implemented: Matched rows now display the immutable GL net amount, unmatched rows display the corrected source Debit/Credit values, and summary rows separately show `Grand Livre source`, `Ajustements actifs`, `Grand Livre après ajustements`, active grid total and difference. BC-only matching now also requires the exact amount; amount-only matching is `Exacte` only when the date also matches, otherwise `Probable`; AI semantic matches are discarded unless amounts match in code. The deployed code passes the expanded 318-test suite, including 46 automatically discovered Grand Livre tests. Live upload 13 values await TR-GL-003's authorized rebuild.
- Live verification: The rendered page shows source/adjusted GL 4753.16, active grid 1076.55 and explained difference 3676.61. Every displayed entry carries its source debit/credit from workbook rows 62-76; the seven matched GL amounts equal their grid amounts, 15 adjustment links are present, and there is no residual or missing-expense section. The service and migrations 0009/0010 are healthy.

## TR-DEPLOY-001 — Prepare Tresorapide for a private online HORTRAME release

- Source: User request on 2026-09-13: determine whether Tresorapide is ready to put online, use the newly accessible HORTRAME `website/tresorapide` area, and make an implementation plan.
- Status: verified
- Current authorization: Readiness audit and implementation planning only. No cloud provisioning, deployment, repository transfer, commit, push, or DNS change is authorized by this request.
- Interpretation: “Online” means a private authenticated production service for financial records, with HORTRAME website integration; it does not mean anonymous public access.
- Readiness finding: Not ready today. The 318-test application is a strong local release candidate and the synthetic production `check --deploy` passes, but the current container still uses `runserver`, applies migrations and seed data at startup, stores media locally, lacks production identity/secrets/storage/monitoring/recovery/IaC, has a large uncommitted change set, has not evidenced the 99% OCR threshold on an independent corpus, and has not completed HORTRAME module/CRAS/integrity/export requirements.
- Architecture finding: `D:\.HORTRAME\git\website\tresorapide` is empty and its parent deploys as a static Netlify site. It should contain only a no-index launcher or redirect. The private Django/PostgreSQL backend belongs in a separate reusable module and private deployment repository/service.
- Acceptance check: Produce a source-bound, ordered implementation plan with explicit production, OCR, accounting/auditability, privacy, recovery, HORTRAME, migration, website, and go/no-go gates; identify decisions needed before provisioning; do not mutate deployment state.
- Evidence: [`ONLINE_DEPLOYMENT_IMPLEMENTATION_PLAN.md`](ONLINE_DEPLOYMENT_IMPLEMENTATION_PLAN.md); Dockerfile and `scripts/docker-start.sh`; `docker-compose.yml`; `config/settings.py`; `requirements.txt`; clean synthetic `manage.py check --deploy`; successful Compose schema validation; current Git status; HORTRAME module/CRAS/lifecycle standards; website `netlify.toml`; official Django, Microsoft Azure, and Netlify deployment documentation cited in the plan.
- Plan result: The implementation is divided into source freeze, HORTRAME module registration, production hardening, durable/cost-bounded OCR, database-level consequential-record controls, isolated infrastructure, staging/recovery validation, private data migration/pilot, and a tightly allowlisted website entry-point change. Production remains blocked until every non-negotiable release gate is evidenced.

## TR-EXPORT-001 — Include month and day in expense-grid export filenames

- Source: User request and save-dialog screenshot on 2026-09-13: PDF/XLSX filenames need the month and day as well as the year.
- Status: verified
- Interpretation: This applies to the expense-grid exports shown in the screenshot, whose current names are `Grille_depenses_<house>_<year>.pdf|xlsx`. Replace the trailing year-only value with the local save/export date in unambiguous ISO order: `Grille_depenses_<house>_YYYY-MM-DD.pdf|xlsx`.
- Acceptance check: Both PDF and XLSX response filenames contain the house code and local export date as `YYYY-MM-DD`; PDF/XLSX contents and BC-specific exports are unchanged; tests freeze the date and assert both exact headers.
- Evidence links: `budget/views.py`; `budget/tests.py`; supplied save-dialog screenshot; focused and complete test results; rebuilt-container response headers.
- Verification: Both expense-grid views now use Django's configured local date and emit `Grille_depenses_<house>_YYYY-MM-DD.pdf|xlsx`. The two response-header tests freeze the date and assert the complete filename while retaining the PDF/XLSX format checks. The focused export class passes 5/5 and the complete suite passes 318/318 using an isolated writable test-media directory. The local Docker image was rebuilt; both containers are healthy and read-only requests against the deployed web container returned `Grille_depenses_BB_2026-09-13.pdf` and `Grille_depenses_BB_2026-09-13.xlsx`. BC-specific export names were not changed.

## TR-GL-006 — Tresorapide purchase dates take priority over GL posting dates

- Source: User clarification on 2026-09-13: Tresorapide dates may differ from the Grand Livre and that is acceptable; Tresorapide dates have priority.
- Status: verified
- Confirmed rule: Keep the source/purchase date stored from the BC or receipt in Tresorapide. A differing or missing GL posting date does not overwrite it and does not break a BC-number-plus-amount reconciliation. Preserve the GL date separately as accounting-source evidence.
- Acceptance check: Reconciliation and member-facing exports retain Tresorapide dates, keep GL dates confined to the reconciliation view/source record, and never rewrite a purchase date merely to match accounting.
- Evidence links: user clarification; `budget/gl_reconciliation.py`; `budget/export_service.py`; active upload 13 comparison.
- Verification: The current PDF and XLSX expense-grid exports use the seven Tresorapide purchase dates (`2026-01-07` through `2026-06-17`) and do not substitute the later/blank dates from upload 13. The GL rows preserve their separate accounting dates in reconciliation. This is the required behavior.

## TR-DIST-001 — Verify the current expense-grid exports for member distribution

- Source: User question on 2026-09-13: “Is it good enough to send to the members?”
- Status: verified
- Scope: Read-only release review of the current live BB 2026 PDF and XLSX exports. This does not authorize sending files or changing their financial content.
- Acceptance check: Generate the current exports from the deployed container; verify filenames, complete active-row population, purchase dates, identifiers, amounts, running balances and summary totals against the live database; inspect every PDF page and every XLSX worksheet for clipping, corruption, formula errors and sensitive/internal-only content; give a candid send/no-send decision with any blocker.
- Evidence links: deployed export responses; generated QA artifacts; live database controls; PDF renders; workbook structure/value/formula inspection.
- Financial verification: Both formats contain the same seven active expenses and Tresorapide dates. Their amounts sum to `1076.55`; the running balance ends at `11160.45` and the balance after the 15% reserve ends at `9889.82`. The summary repeats those controls. Non-contingency used amounts reconcile to `511.63`; adding contingency use `564.92` gives `1076.55`. The category rows independently reproduce every expense allocation. No cancelled/test row appears.
- File verification: The PDF is a valid unencrypted two-page letter-landscape document with no JavaScript. Its complete text is extractable and both pages were visually inspected. The XLSX imports successfully with two worksheets (`Grille de dépenses`, `Résumé budgétaire`), contains no formulas, and has zero spreadsheet error tokens.
- Distribution finding: Not yet sendable at the requested quality bar. The PDF's seven `GL` checkmarks exist in extracted text but do not render in Poppler; it reports `No display font for 'Symbol'`, so the visible GL column is blank in a common renderer. The XLSX contains all values, including the checkmarks, but multiple member/supplier/description/validator fields are visibly clipped because the data rows do not wrap and the columns are too narrow. The PDF's second page is also mostly blank, though that is secondary. The exports disclose member names and apartment numbers, so distribution still depends on those members being an authorized audience.
- Reassessment after TR-PDF-001: The current PDF is financially and visually suitable for authorized cooperative members. The XLSX still preserves every value but remains visibly clipped in several text columns and has not received the member-facing layout repair. Therefore: send the PDF; do not treat the XLSX as the polished member copy yet.

## TR-PDF-001 — Redesign the member PDF for simple, low-friction reading

- Source: User instruction on 2026-09-13: put `Résumé budgétaire` to the left of `Sous-budgets`, place both on their own page, retain all existing information, and make the report much easier for members with limited comfort reading financial reports.
- Status: verified
- Confirmed scope: Redesign the PDF export only. Page 1 keeps the complete expense ledger. Page 2 presents the complete budget summary at left and complete sub-budget table at right. Add concise plain-language orientation, stronger hierarchy and explanations without removing or changing any source value.
- Accessibility interpretation: Optimize for cognitive ease without patronizing language: clear French labels, a small number of headline figures, consistent reading order, sufficient type/spacing/contrast, restrained color, and definitions for `GL`, the 15% reserve and negative category balances.
- Acceptance check: All existing ledger columns, seven active rows, 12 budget-summary measures and all 14 sub-budget rows remain; summary and sub-budgets share a dedicated second page in the required left/right order; GL status renders in common PDF viewers; no clipping/overlap/broken glyphs; amounts independently reconcile; every final page is rendered and visually inspected.
- Evidence links: `budget/export_service.py`; `budget/tests.py`; final two-page PDF; 180-DPI renders; source-bound focused/full tests; deployed-container health and generation checks.
- Verification: Page 1 now opens with four plain-language figures, keeps all 12 ledger columns and seven active rows, uses readable `Oui` text for every GL match, identifies the dates as Tresorapide purchase dates, and explains both GL status and the 15% reserve. Page 2 is dedicated to `Comprendre le budget`; the complete 12-line `Résumé budgétaire` is on the left and the complete 14-category `Sous-budgets` table is on the right. The negative `Autre dépenses` remainder is red and explained. French-Canadian money formatting, generation date and page numbers were added. The final two-page PDF was rendered at 180 DPI and both pages were inspected at original resolution: no clipping, overlap, missing status, or broken visible glyph remains.
- Numerical controls: Seven active expenses total `1076.55`; page-1 ending balances are `11160.45` and `9889.82`; page 2 reports non-contingency planned/used/remaining totals of `10775.00`, `511.63`, and `10263.37`. The values reconcile to the database and prior member-export audit.
- Automated/deployment evidence: A new PDF regression extracts both pages, confirms all summary labels and content boundaries, and verifies `Résumé budgétaire` is physically left of `Sous-budgets`. Focused export tests pass 6/6; the complete suite passes 319/319. The final Docker image was rebuilt, both local containers are healthy, and the deployed generator produced the reviewed artifact. QA artifact SHA-256: `510F3B11CFCF1778E23A2BBF2F6750EDD333DA277D57FDDDE5368747A4348B5C`.

## TR-PDF-002 — Optional changes-since-committee summary

- Source: User suggestion on 2026-09-13: possibly summarize what changed since the last `comité de copropriété`, while accounting for multiple draft exports before a report is actually sent.
- Status: captured
- Tentative scope: Add a member-readable change summary only after defining a reliable comparison baseline. Do not infer “last committee” from the immediately previous download because it may be an unsent draft.
- Baseline decision requested: Compare with the last export explicitly marked as sent to the committee, or with a user-selected cutoff/date on each export.
- Acceptance check: A confirmed baseline is persistently identified; repeated draft exports do not advance it; the summary reports additions, corrections, cancellations, amount/category changes and budget impact from attributable records; no AI-generated narrative can alter source amounts or imply unsupported reasons.
- Evidence links: user instruction; export workflow; future snapshot/event model and tests after baseline choice.

## TR-GL-007 — Materialize every Grand Livre entry in Tresorapide and member exports

- Source: User correction and Grand Livre screenshot on 2026-09-13: much of the Grand Livre is missing; every GL entry must appear, while entries already present in Tresorapide keep the Tresorapide descriptions.
- Status: verified
- Confirmed merge precedence: The Grand Livre defines the complete accounting population and exact amount for each source row. For an already matched Tresorapide expense, keep the Tresorapide purchase date, description, supplier/member attribution, category and other local fields. Preserve the separate GL date/description as source evidence. For an unmatched GL row, create one attributable active Tresorapide expense from the GL date, amount, BC/receipt identifier and description; never create it twice.
- Confirmed source scope: Use only account `13-51200`, the account already stored for house BB in the Tresorapide database. Do not import another account block, worksheet header, running-balance cell or total row.
- Automation requirement: The user explicitly requires this merge to happen automatically and to be validated. On each accepted reconciliation for the house account, match existing local expenses first, then materialize every remaining nonzero GL source row exactly once. A repeated reconciliation/re-upload must preserve or recover links without creating duplicate expenses.
- Automatic classification rule: Reuse an existing/local sub-budget when the same BC proves the relationship; otherwise use an apartment-specific category only when the GL carries an apartment number, use only narrow deterministic category keywords, and route anything not provable to `Autre dépenses` rather than guessing.
- Current gap: Upload 13 contains 15 source entries totalling `4753.16`. Seven are matched to active Tresorapide expenses totalling `1076.55`; eight accounting-only rows totalling `3676.61` are absent from the expense grid and therefore from the member PDF/XLSX.
- Acceptance check: Back up the live database; prove the eight-row import manifest against workbook rows 66-68, 70-72, 75-76; import atomically and idempotently with source links/audit evidence; retain every field of the seven local matches; rebuild reconciliation to 15/15 represented, zero GL-only, zero grid-only and exact `4753.16` active total; verify the final grid, PDF and XLSX contain all 15 entries and reconcile their running balances/category totals; render every PDF page and inspect every worksheet.
- Evidence links: authoritative Grand Livre workbook and screenshot; upload 13 entries; `budget/gl_reconciliation.py`; migration 0011; import tests; protected backup; source-bound import manifest and audit entries; final PDF/XLSX artifacts and renders.
- Implementation: `full_reconciliation()` now parses the house account, matches existing expenses first, then automatically materializes every remaining effective GL row before it builds the reconciliation. Account validation fails closed unless the imported account equals the house's stored account. GL-created expenses carry a deterministic source fingerprint unique within the budget year, so a reprocessed upload or a second upload of the same source row relinks instead of duplicating it. Actual credits are retained as active negative GL expenses; a source row fully neutralized by an approved adjustment remains in the GL/adjustment view and does not create a meaningless zero-dollar grid row.
- Merge precedence verification: All seven pre-existing matches retain the exact Tresorapide expense ID, purchase date, description, supplier, spender, trace and amount captured before import. The eight new rows use their GL dates/amounts/identifiers and cleaned source descriptions. Rows 66-67 inherit trace 1 from their shared BC/apartment evidence; row 71 uses trace 11 (`Peinture`); row 76 uses trace 5 (`Exterminateur`); rows 68, 70, 72 and 75 use trace 99 (`Autre dépenses`) because the source does not prove a narrower category.
- Safety evidence: Protected pre-import backup `C:\Users\gombo\AppData\Local\Tresorapide\backups\20260913-114632-699972`. SHA-256: database `DD39AFE021C8660FE897825D9E15CBE9D9B0A02AF2D96BCB1EF9BA6A2FF39DEA`; media `D2697E59FB2CFA1DCEC6F9BEE65943E62D8A8338FB20DECDCD748B8BE5D50C4B`; metadata `9DA48F0A38C7C5F5EB09C169FD6FF40FED0856D322C937524EE61F9F04B08EF3`. Source workbook SHA-256 remained `76365E211D5A6C95213060C6B1697AC8BCA149483F5AF93CE59FF23FF477FE8F` before apply.
- Live result: Atomic execution created expenses 105-112 and eight `grand_livre.entry_materialized` audit entries plus one account-level audit manifest. Upload 13/account `13-51200` now has 15 source rows, 15 active expenses, 15 matches, 0 GL-only rows, 0 grid-only rows, GL total `4753.16`, grid total `4753.16`, difference `0.00`, and balanced status. The apply transaction itself reran materialization and proved zero additional creations.
- Automated verification: Account mismatch, local-field precedence, same-BC category inheritance, conservative fallback, exact fingerprint relinking across a second upload, repeated-run idempotency, audit creation, actual-credit handling and full-adjustment behavior are covered. Focused reconciliation/adjustment tests pass 26/26; the complete suite passes 325/325. Migration 0011 is applied and both deployed containers are healthy.
- Artifact verification: The final PDF contains 15 rows and the required eight newly materialized descriptions; every GL row displays `Oui`; page 2 remains dedicated to the budget summary/sub-budgets. Its headline controls are expenses `4753.16`, available `7483.84`, and available after reserve `6213.21`; both pages were rendered at 180 DPI and inspected without clipping or missing rows. The XLSX contains 15 data rows across `A5:L19`, the same totals, two valid worksheets and zero spreadsheet error tokens. Final SHA-256: PDF `6E208D65B4774AF11EDFE25E808E47794496B0A5DD4261990B97E6C530EF6785`; XLSX `A78BAF2BE00A9838DDFF7302DEE9368EB0756A487A5120D9E27D6D0842FF4093`.

## TR-GL-008 — Attribute GL imports to apartment blocks or BB

- Source: User correction and annotated Grand Livre screenshots on 2026-09-13: `102` and `103` following `#` are apartment numbers; subsequent purchases in the same accounting period inherit that apartment; entries without an indicated apartment use `BB`; the `Dépensé par` fallback is specifically `BB`, never `CHCE`.
- Status: verified
- Screenshot-grounded interpretation: The examples span different calendar dates but share a merged `Ann/Pér` block, so propagation follows the ordered monthly accounting-period block (`Feb-26`, `Mar-26`), not literal same-day equality. A later explicit `#<apartment>` changes the inherited apartment for the remainder of that period. The inheritance resets at the next period.
- Merge boundary: Apply the rule to GL-imported/direct-accounting expenses. Preserve a member/BC expense's existing Tresorapide purchaser attribution even when it is matched to a GL row in the same period.
- Acceptance check: Resolve `#102` and `#103` to the corresponding apartment/member display labels from the BB database; row 64 uses apartment 102; GL-import rows 66-68 use apartment 103; all other GL-import rows in upload 13 use `BB`; no `CHCE` remains in `Dépensé par` for GL imports; future reconciliation applies the same ordered period propagation automatically; exact amounts, descriptions, dates, matching and totals remain unchanged; tests and final PDF/XLSX verify the rule.
- Evidence links: annotated screenshots; account 13-51200 rows 62-76; apartment/residency records; `budget/gl_reconciliation.py`; source-hash-guarded synchronization script; protected backup; per-row and batch audit records; final PDF/XLSX and renders.
- Database resolution: Apartment 102's primary contact on the February dates is Melyse Mupfasoni. Apartment 103's primary contact on the March dates is Carole Lacourse. The ordered source periods are `2026-02` for rows 64-65 and `2026-03` for rows 66-68; propagation resets when the period changes.
- Automatic implementation: Parsing now records an `apartment_context` for every GL row (`explicit`, `period_inherited`, `extracted` or `none`), carries the latest explicit apartment through the remainder of the same period, and resets at the next period. New GL imports derive `spent_by_label` from the apartment/contact on the source date or fall back to the house code. Every full reconciliation also synchronizes prior GL-imported expenses, but preserves member/BC expenses. `display_approved_by_label` is independent: GL imports show `CHCE` as validator while `Dépensé par` shows apartment/BB.
- Live correction: Source-hash-guarded atomic execution changed ten GL-imported expenses. Row 63 is `BB`; row 64 is `102 / Melyse Mupfasoni`; rows 66-68 are `103 / Carole Lacourse`; rows 70-72 and 75-76 are `BB`. Expense 107/row 68 moved from trace 99 to trace 1 because the March apartment context is now proven. The five member/BC rows 62, 65, 69, 73 and 74 retained their prior spender, description and trace exactly. The operation produced ten per-row `grand_livre.import_context_synchronized` audit entries and one batch audit; its built-in second pass produced zero further changes.
- Financial verification: The recategorization changes only category presentation. Account 13-51200 remains 15/15 matched; Grand Livre and active grid are both `4753.16`, difference `0.00`, with zero GL-only and zero grid-only rows. Repairs used are now `724.63`; trace 1 used/remaining is `377.27`/`1622.73`; trace 99 used/remaining is `336.68`/`-336.68`; overall available totals remain `7483.84` and `6213.21` after reserve.
- Safety evidence: Protected pre-correction backup `C:\Users\gombo\AppData\Local\Tresorapide\backups\20260913-123833-603309`. SHA-256: database `65DC310D2EC5A13FA3015D450F99148A6E069AE5C845F419C587CB28E2E40D88`; media `E9D00DCE7A30E583846DF3FEF89C45ABA7BAD9726DC65D8EB4F22458F7FB3E71`; metadata `6F032659407C7DC3E1FAA28C4E0E1497826CE48AA396C8FDF1081933FCEE9086`.
- Test/deployment verification: Explicit/inherited/reset behavior, BB fallback, primary-contact resolution, CHCE validator separation, conservative category synchronization, member-row preservation, audit creation and idempotent second pass are covered. The focused attribution/reconciliation set passes 24/24; the complete suite passes 327/327. The rebuilt local web and database containers are healthy.
- Artifact verification: The final PDF shows the exact apartment/BB labels without clipping on page 1 and the corrected trace totals on page 2; both pages were rendered at 180 DPI and visually inspected. The XLSX contains the same labels in `Dépensé par`, `CHCE` only in `Validé par`, all 15 rows and zero spreadsheet error tokens. Final SHA-256: PDF `54BEF39AA0CCC9AD29384C845EF59DB2095764A6EF3D9A315C2EEB991048E9C3`; XLSX `6C2AEE1E28CA1161F1A3CD2AF364D9D3E52ADB68F22374BE1EBC716D4F10A3C3`.

## TR-UI-001 — Add a discreet HORTRAME identity banner

- Source: User request on 2026-09-13: inspect how HORTRAME is displayed on the Swarm page and use it as inspiration for a small discreet banner at the top of Tresorapide.
- Status: verified
- Reference evidence: The Swarm page uses a 52-54 px near-black identity strip above its own product header, a compact geometric HORTRAME mark, muted blue-gray text, brighter `HORTRAME` wordmark, fine bottom border and restrained focus/hover treatment. Source: `D:\.HORTRAME\git\website\swarm\index.html` and `research-nav.css`.
- Confirmed adaptation: Add a smaller light-weight strip above Tresorapide's blue header, reuse the exact inline geometric mark and `HORTRAME: Accelerating Epistemological Alignment` identity, preserve the existing Tresorapide navigation, and provide responsive/focus behavior without animation.
- Acceptance check: Banner appears consistently on logged-in, logged-out and compact mobile pages; it is visually subordinate to Tresorapide; the link/mark has an accessible label; no layout shift, clipping or navigation regression occurs; desktop and mobile browser renders are inspected.
- Evidence links: Swarm identity markup/CSS; `templates/base.html`; `static/css/site.css`; foundation/mobile template tests; deployed in-app-browser accessibility tree and desktop capture.
- Implementation: `base.html` now places a 38 px near-black HORTRAME identity strip above the existing Tresorapide header. It reuses the Swarm page's exact inline geometric mark and `HORTRAME: Accelerating Epistemological Alignment` identity, muted/bright text hierarchy, thin divider and teal focus treatment. It links to the public HORTRAME homepage in a separate safe tab. A 600 px breakpoint reduces the strip, mark, gap and type without hiding the identity.
- Verification: The base-template tests confirm the banner, canonical URL, inline mark and accessible label; a mobile-user-agent regression confirms it remains on compact pages. The final deployed desktop browser capture shows the strip centered, visually subordinate and separated cleanly from the blue Tresorapide navigation, with no clipping or header shift. The browser accessibility tree exposes one correctly named HORTRAME link before the Tresorapide navigation. The final full suite passes 331/331 and both containers are healthy.

## TR-MEMBER-001 — Attribute purchases by residency on the purchase date

- Source: User question on 2026-09-13: members may move during the year; an old purchase must remain with the former resident and future purchases must go to the new resident.
- Status: verified
- Audit finding: Residency records and validated BC relations/snapshots preserve history, and the new GL import path already resolves apartment contacts using the GL transaction date. However, receipt/paper-BC OCR review helpers (`_match_member_for_apartment`, `_match_member_by_name`, `_resolve_member_assignment`, `_save_paper_bc_extracted_fields` and sync paths) still use current/open-ended residency. An old document reviewed after a move can therefore resolve to the new occupant.
- Confirmed rule: Resolve a document against the residency active on its purchase date. Ending dates are inclusive; a purchase before/on the old residency end remains with the old member, and a purchase on/after the new residency start goes to the new member. Once validated, the purchase keeps its stored member/apartment snapshots; later moves do not rewrite it.
- Acceptance check: Date-aware apartment and name resolution is used in single/bulk receipt review, paper-BC signer resolution, existing-bon synchronization and automatic apartment matching; historical members remain selectable for historical documents; OCR context includes residency periods rather than only current members; boundary tests cover day before move, old final day, new first day and later purchases; existing validated purchases remain unchanged after a move.
- Evidence links: `members/models.py`; `bons/models.py`; `bons/ocr_service.py`; `bons/views.py`; existing seed move-preservation test; new dated-resolution, signer, snapshot and prompt regressions.
- Pre-fix answer: Not completely. Residency history and fixed BC member/apartment relations already existed, but receipt/paper-BC OCR resolution used only the current open-ended residency and current active member directory. Reviewing an older purchase after an apartment turnover could assign the new occupant.
- Implementation: Apartment lookup, fuzzy name lookup, member assignment, signer assignment and validator assignment now accept the document purchase date and query `Residency.active_on(date)`. The last day of an old residency is inclusive; the new resident begins on the stored start date. Single and bulk OCR review pass the extracted/final purchase date, paper-BC synchronization does the same, and automatic receipt-to-member assignment falls back to the bon date. When a selected signer is saved, the apartment is resolved through that member's residency on the document date rather than their current apartment. Review forms include historical house members so a moved/deactivated former resident remains selectable for an old document.
- OCR context: The model prompt no longer contains only current members. It lists every house residency with apartment, official member name, start and inclusive end (`présent` when open), and explicitly instructs date-of-purchase selection. Deterministic application code remains authoritative after OCR.
- Boundary verification: Tests model one apartment with Serge through 2026-06-30 and a new member from 2026-07-01. A 2026-06-30 purchase resolves to Serge even after he is inactive; a 2026-07-01 purchase resolves to the new resident; historical fuzzy-name resolution returns Serge and the old apartment; paper-BC signer resolution switches on the same boundary. A validated old bon retains `105 / Serge Laroche` and its frozen name/unit snapshots after the move. Existing current-member and external-validator tests remain green.
- GL consistency: The account 13-51200 importer already resolves apartment contacts using each GL transaction date and stores the resulting label. Later residency changes therefore select the resident active on the source date, while already materialized audit history remains attributable. No financial record or total was changed by this member-resolution deployment.
- Verification: Focused banner/residency tests pass 11/11; the complete application suite passes 331/331. Django checks and the final rebuilt local containers are healthy.

## TR-GL-009 — Persist manual spender overrides for Scellant and emergency battery

- Source: User instruction and annotated member-report photo on 2026-09-13: `Scellant` must be under Marylin; `Batterie d'urgence pour l'éclairage` must be under Carl-David; return the corrected PDF.
- Status: verified
- Confirmed targets: Upload 13/account 13-51200 row 67, expense 106, `9.30`, becomes `202 / Marylin Lamarche`. Row 68, expense 107, `124.12`, becomes `206 / Carl-David Fortin`. Descriptions, dates, amounts, GL links and trace 1 remain unchanged.
- Persistence requirement: These are user-authoritative exceptions to the automatic March apartment-103 inheritance. Store an explicit GL spender-override flag/reason on the expense so current and future reconciliation synchronization cannot overwrite them. Preserve the inherited apartment context separately on the GL source rows.
- Acceptance check: Take a protected pre-change backup; add tested override persistence; apply both changes atomically with per-row audit evidence; rerun automatic synchronization and prove the labels survive; keep 15/15 matches and `4753.16`/`4753.16`/`0.00` controls; generate the dated PDF from the deployed code, extract the two exact label/description/amount combinations, render every page and visually inspect before delivery.
- Evidence links: annotated photo; upload 13 rows 67-68; expenses 106-107; migration 0012; override/synchronization/form tests; protected backup; source-hash-guarded apply script and audit records; final PDF and renders.
- Persistence implementation: Migration 0012 adds `gl_spent_by_override` and its reason to expenses. Automatic GL context synchronization now preserves a manually confirmed spender while continuing to update non-overridden imports. Editing `Dépensé par` on any GL-import expense through the normal form sets the persistent override and writes an attributed audit record.
- Safety evidence: Protected pre-change backup `C:\Users\gombo\AppData\Local\Tresorapide\backups\20260913-143721-263150`. SHA-256: database `7C38B495922053822C2EE5C50578F6B1D864829DE3AC214D63F2EA8BE6444619`; media `85BE386087C8634DF3B0C6F7FECDE9FB25FA6E3A12513F5775C1296B1E19E917`; metadata `3C3680799DD72CAFC9891E14AB01BECFE816FD23353DE1E4A6661BEAD05B58B1`. The apply script revalidated source workbook SHA-256 `76365E211D5A6C95213060C6B1697AC8BCA149483F5AF93CE59FF23FF477FE8F`, account 13-51200, row IDs, expense IDs, dates, descriptions, amounts, trace and member residency before writing.
- Live result: Atomic execution changed exactly two rows. Expense 106/row 67 is `202 / Marylin Lamarche`; expense 107/row 68 is `206 / Carl-David Fortin`. Both carry the manual-override flag and user-confirmed reason. Two per-row `grand_livre.spent_by_overridden` events and one batch audit preserve before/after/source evidence. An immediate automatic context synchronization produced zero changes, proving the March 103 inheritance cannot overwrite the exceptions.
- Financial verification: Descriptions, dates, amounts, trace 1, source fingerprints and GL links are unchanged. Account 13-51200 remains 15/15 matched; Grand Livre and grid totals remain `4753.16`; difference remains `0.00`; headline available amounts remain `7483.84` and `6213.21` after reserve.
- Test/deployment verification: Manual override persistence in reconciliation and ordinary expense-form edits is covered. The complete application suite passes 333/333; migration 0012 is applied; Django checks pass; both containers are healthy.
- PDF verification: Final `Grille_depenses_BB_2026-09-13.pdf` is a valid unencrypted two-page letter-landscape PDF. Table extraction confirms the `Scellant` row has date `2026-03-20`, spender `202 / Marylin Lamarche`, amount `9.30`; the battery row has date `2026-03-31`, spender `206 / Carl-David Fortin`, amount `124.12`. All 15 rows and summary controls remain. Both pages were rendered at 180 DPI and inspected without clipping, overlap or missing fields. Final SHA-256: `195B3B8366570BA694D3828AD24C5ED6440579E296B309B35076474372A29AB2`.

## TR-DIST-002 — Save the final member PDF as an unsent Gmail draft

- Source: User request on 2026-09-13 to save the corrected PDF as an email draft, followed by explicit selection of the Personal Gmail account.
- Status: verified
- Confirmed scope: Create one unsent draft in `gombolduc@gmail.com`; leave To, Cc and Bcc empty; attach `output/pdf/Grille_depenses_BB_2026-09-13.pdf`; use a clear French subject and concise member-facing body. Do not send the message.
- Acceptance check: The draft exists in the Personal account, remains unsent with no recipients, and contains the exact final PDF whose SHA-256 is `195B3B8366570BA694D3828AD24C5ED6440579E296B309B35076474372A29AB2`.
- Evidence links: Gmail draft record and MIME attachment metadata; local final PDF and its verified SHA-256.
- Result: Gmail created draft `r-5667097232163341143` in `gombolduc@gmail.com` with underlying message `1a09c181b2a934b1`. Direct readback shows the `DRAFT` label, no To/Cc/Bcc headers, the expected French subject/body, and one `application/pdf` attachment named `Grille_depenses_BB_2026-09-13.pdf`. Gmail reports 9,818 attachment bytes, exactly matching the verified local artifact size, and exposes the original attachment reference. No send action was invoked.

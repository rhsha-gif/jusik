# KIS paper execution: OSS evidence — 2026-09-11

## Revised conclusion

Broker clarification is not the only defensible next step. Existing open-source implementations support a separate full-remaining cancellation request without the unsupported paper cancelable-order inquiry. Implementing that explicit contract and verifying it in a bounded KIS paper session is justified. This is not evidence that our account has completed a trade or that the current runtime can be enabled unchanged.

The owner requires KIS paper-account execution and broker-confirmed fills. Local simulated fills are excluded. No broker writes, runtime enablement, external messages or third-party code execution occurred during this research.

## Inspected implementations

Source snapshots were pinned; implementation, relevant tests and actual license text were inspected. Public source files were downloaded outside the repository without installing/importing the projects.

| Project / revision | Actual handling | Evidence limits |
| --- | --- | --- |
| [python-kis](https://github.com/Soju06/python-kis/blob/3be3d012a4398cded24edf761351516a0d7d7083/pykis/api/account/order_modify.py#L191), `3be3d012a4398cded24edf761351516a0d7d7083` | `domestic_cancel_order` directly sends original branch/order identifiers, quantity 0, price 0, full-remaining Y and order division 00. No pending inquiry first. The separate domestic modify function rejects virtual mode because it depends on pending inquiry. | Uses legacy paper TR `VTTC0803U`. Source establishes implemented behavior, not independently reproduced broker success. |
| [ante](https://github.com/joshua-jingu-lee/ante/blob/69492e61e16b76ca72b1c36cc608cd93f28777c5/src/ante/broker/kis.py#L1460), `69492e61e16b76ca72b1c36cc608cd93f28777c5` | Uses current `VTTC0013U`, KRX, quantity 0, price 0, full-remaining Y. Captures original forwarding organization number from order submission and reuses a same-business-day cache entry. Order division is 01. | Maintainer reports a successful paper-account cancellation. Cache is in memory; cancellation still sends with the forwarding field omitted on cache miss/stale date. A successful method return does not itself reconcile final state. |
| [mojito](https://github.com/sharebook-kr/mojito/blob/da87f1470b2b6f178138f506c30248832473c608/mojito/koreainvestment.py#L1232), `da87f1470b2b6f178138f506c30248832473c608` | Caller supplies original organization/order IDs. `cancel_order` delegates to `update_order`, with full-remaining Y when requested and caller-supplied quantity/price; no native inquiry prerequisite inside cancellation. | Legacy `VTTC0803U`; returns raw response. Separate `fetch_open_order` hardcodes real `TTTC8036R`, so this does not establish paper pending-order support. |

### Operational evidence and tests

Ante [issue #2346](https://github.com/joshua-jingu-lee/ante/issues/2346) reports a 2026-06-12 A/B on a paper account: both legacy and current cancellation routes accepted, with current-route response `40630000`. [Merged PR #2363](https://github.com/joshua-jingu-lee/ante/pull/2363) records the resulting migration. Although the project calls this a live A/B, the account is explicitly paper. This is a maintainer's observation, not our reproduction or a formal broker interpretation.

Ante's [fake-client cancellation tests](https://github.com/joshua-jingu-lee/ante/blob/69492e61e16b76ca72b1c36cc608cd93f28777c5/tests/unit/test_kis_cancel_krx_fwdg_ord_orgno.py) lock current TR selection, body fields and forwarding-ID reuse. They also deliberately accept a cache miss by omitting the forwarding field. Those tests were read, not executed; mocked success is not broker acceptance evidence.

Ante [daily history retrieval](https://github.com/joshua-jingu-lee/ante/blob/69492e61e16b76ca72b1c36cc608cd93f28777c5/src/ante/broker/kis.py#L1753) paginates daily orders/fills using current paper TR `VTTC0081R` for recent dates. Its fold groups by order number and date and retains maximum cumulative fills. Its fallback from missing fill price to order limit price and its `filled` label for any positive cumulative quantity must not be copied into QuantPilot's strict fill/state handling.

## Application to QuantPilot

1. Discover working orders from fully paginated daily order/fill evidence and reconcile account holdings. Keep daily remaining quantity distinct from native cancelable quantity; never convert `rmn_qty` into `psbl_qty`.
2. Introduce an explicit paper full-remaining cancellation operation: current `VTTC0013U`, KRX, `RVSE_CNCL_DVSN_CD=02`, `QTY_ALL_ORD_YN=Y`, quantity 0 and price 0. For our limit-only path, validate order division 00; ante's reported division-01 success does not establish this exact combination.
3. Require original order-submission forwarding ID and original order number, durably scoped to account and business date. Do not replace the forwarding ID with the daily-query branch number. Missing, conflicting or stale identity blocks submission.
4. Preserve durable claim-before-POST and at-most-once dispatch. An ambiguous response enters an unresolved state and recovers through queries; never blindly retry a cancellation POST. Do not adopt upstream generic retry policy as our order contract.
5. Treat cancellation acknowledgement as acknowledgement. Only subsequent broker cumulative fills, confirmed canceled quantity, remaining quantity and holdings establish final state. Preserve partial fills, late fills, idempotent accounting and unresolved-state entry blocking.

Existing pre-trade risk, kill switch, audit, reconciliation and live-disabled defaults remain requirements. No new SDK dependency is needed to implement this small transport/state contract in the existing adapter.

## Verification remaining

Before paper startup, implementation needs deterministic tests for pagination failures, unknown external orders, missing/conflicting original identifiers, partial/late fills, cancellation/fill races, timeouts, restarts and duplicate attempts. Required project checks and independent safety review apply to execution changes.

Then use the approved KIS paper runtime for a bounded session. Success requires broker order acceptance plus reconciled fill/cancel quantities and holdings, not merely a successful POST. The final evidence must identify which cases were actually observed; a full fill alone does not prove partial-fill cancellation behavior.

Independent read-only review agrees that OSS evidence replaces the prior mandatory broker-question prerequisite with implementation plus bounded paper verification. It does not grant operational approval to unmodified code. The trader has not been restarted by this research.

## Reuse and license

All three inspected license files are MIT (ante: 2025-present Joshua-Jingu-Lee; python-kis: 2024 Soju06; mojito: 2022 sharebook-kr). No upstream code has been copied into QuantPilot. If subsequent work copies substantial code, retain the corresponding copyright and permission notice in that change. Prefer implementing the narrow request pattern inside the existing adapter rather than importing an entire SDK and its transport/state assumptions.

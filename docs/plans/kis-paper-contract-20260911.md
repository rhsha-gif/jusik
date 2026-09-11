# KIS paper fill contract — 2026-09-11

The owner requires execution and fill evidence from the KIS paper account. Local simulation was explicitly rejected and its unexecuted prototype was removed. Live trading remains disabled. Commit `28bdf52` is retained; no remote push was authorized.

## Verified broker contract

The [official API collection](https://apiportal.koreainvestment.com/files/download/apiCollection/API_COLLECTION), downloaded on 2026-09-11, documents:

| Operation | Paper support |
| --- | --- |
| Daily orders and fills | `VTTC0081R`, paginated, up to 15 rows per page |
| Full-remaining cancellation | `VTTC0013U`, `QTY_ALL_ORD_YN=Y` |
| Cancelable-order inquiry | Explicitly unsupported in paper |

Both the collection and the [official cancellation example](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/order_rvsecncl/order_rvsecncl.py) nevertheless require checking native `psbl_qty` before cancellation. The `Y` field is described as full remaining, but the required `ORD_QTY` value and exemption from native inquiry are not specified for paper.

Daily `rmn_qty` is remaining quantity, not native cancelable quantity. The rejected conversion patch remains outside the repository and is not applied. Daily rows can support working-order discovery and broker fill reconciliation; they cannot silently acquire cancellation authority.

## Parser correction

The current portal contract names the cancellation confirmation quantity `cncl_cfrm_qty`; older examples and existing fixtures use `cnc_cfrm_qty`. The parser accepts either exact spelling, requires one, and rejects conflicting or invalid values. It does not default missing evidence to zero.

The portal's full-rejection response example leaves `cncl_yn` blank. A blank flag is accepted only when a positive original quantity equals the rejected quantity, with zero filled quantity, remaining quantity, confirmed cancellation, fill amount and average fill price. All other blank/unknown flags remain errors. No order, cancel, risk or enablement path changes.

Regression tests cover both spellings, conflicts, missing/negative/nonfinite/fractional/empty quantities, the official full-rejection shape and ambiguous blank flags. Unit tests use existing fake identifiers and require no credentials or network.

## Runtime evidence and remaining prerequisite

**Superseded prerequisite:** Subsequent [OSS implementation research](kis-paper-oss-research-20260911.md) found direct full-remaining cancellation paths and a maintainer-reported paper-account success with the current TR. Broker clarification is no longer treated as the sole route forward. The historical question below remains unsent; the next prerequisite is implementation, independent review and bounded KIS paper verification. No startup or successful paper fill is established by this revision.

Read-only balance and daily-order probes passed again. Native unsupported inquiry is no longer repeatedly called by the external diagnostic script. The trader remains stopped with its control paused; no new order was submitted. Minute collection continues separately with no order authority. These observations do not establish a successful paper trading session.

Independent architecture review identified the same cancellation contract gap. The cross-provider design review exhausted its turn limit without a final receipt; it is not a PASS. The parser correction receives separate verification/review and does not authorize trading.

The question formulated before the OSS investigation was:

> 모의투자에서 주식정정취소가능주문조회는 미지원인데, `VTTC0013U` 전량취소(`QTY_ALL_ORD_YN=Y`)를 원주문 접수 식별자와 일별 주문·체결 조회로 확인한 뒤 호출해도 되나요? 이 경우 `ORD_QTY`에는 어떤 값을 보내야 하며, 취소 요청 중 추가 체결이 발생하면 어느 잔량을 대상으로 처리되나요?

This question has not been sent externally. The earlier conclusion that a broker answer was mandatory was too strong and is superseded above. Existing fail-closed startup behavior remains intact until the replacement execution path is implemented and verified.

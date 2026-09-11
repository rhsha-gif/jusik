# Intraday ship and session acceptance — 2026-09-11

The owner requested a commit and same-day live operation. Live operation is blocked: the repository has no live broker adapter, and the new strategy has neither the required historical validation nor the 20 qualifying shadow sessions. No live or paper orders were authorized by this release gate. Actual live capital limits remain a separate owner decision from the KRW 5,000,000 paper experiment.

## Session evidence

- The existing read-only paper readiness command passed price, balance, 20-candidate ranking, exchange session, completed minutes, and quotes. It exposes no account identifiers, holdings, amounts, credentials, or order authority.
- The first collector run persisted 580 completed minute bars, then stopped before receiving any stream event. Its fixed `/oauth2/Approval` authentication endpoint was absent from the strict transport's endpoint set.
- The correction enumerates that exact paper endpoint and uses a dedicated authentication transport that rejects all other methods, origins, paths, order and cancellation operations before HTTP. A fake HTTP opener now tests the real transport validation rather than bypassing it with a fake transport.
- Market collection is a separate bounded process outside the repository. It has no order gateway and stops at the exchange close. Data collected after the session opened is a partial session and is not a qualifying full-session shadow day.

## Dependency check

`websockets==15.0.1` is used by the optional paper collector for the synchronous WebSocket client. The existing HTTP transports do not implement WebSocket framing, subscriptions or ping/pong handling. Installed Python is 3.11.15; the project requires Python >=3.11 and this package requires >=3.9. Its [versioned documentation](https://websockets.readthedocs.io/en/15.0.1/intro/index.html) states that it adds no runtime dependencies. The optional paper extra pins the version, while proxy use is disabled explicitly at the call site.

The installed package declares BSD-3-Clause, consistent with the [versioned license](https://websockets.readthedocs.io/en/15.0.1/project/license.html). Redistribution must preserve the copyright, license conditions and disclaimer, and must not imply contributor endorsement. This check covers need, runtime compatibility, use site and license obligations; it is not a vulnerability certification or a reason to upgrade unrelated packages.

## Release evidence

The first ship verification passed 1,548 tests with two skipped manual integrations, mock smoke, gitleaks, semgrep and tach. Its independent Anthropic ship gate passed. The corrective transport change receives a separate regression run and security gate before commit; final evidence is recorded in the ship receipt.

The earlier implementation receipt is historical evidence for the pre-ship code. Its initial review findings were resolved by the final follow-up PASS recorded within that receipt; they are not unresolved findings. The runtime authentication correction is recorded separately here so that prior source hashes are not presented as hashes of this changed code.

## Final observed state before owner response

- Corrective verification passed **1,554 tests, two skipped manual integrations**, mock smoke, gitleaks (0), semgrep (0), and tach (0).
- The corrective independent ship gate returned **BLOCK** under its rule against any new broker POST path. It explicitly acknowledged the fixed paper authentication endpoint, refusal of order/cancel operations and unchanged sole order-submission authority. The owner was asked to approve this authentication change for commit; no answer had arrived when this note was written. No commit or push was performed.
- After authentication was fixed, a bounded probe received public market events but rejected `H0STASP0` with one record and **62 fields** (`market_frame_shape`). The current official KRX example and parser have 59 fields. The additional fields were not silently truncated or mapped. WebSocket collection remains blocked pending a verified schema update.
- An independent, bounded minute-only collector uses the existing `ReadinessTransport` GET endpoints and token authentication. It persists completed minute bars and point-in-time ranking snapshots to `%USERPROFILE%/.quantpilot/intraday-v2/realtime.sqlite3`, with no order gateway or stream authentication exchange. It ends at the 2026-09-11 KRX close, 15:30 KST.
- The corrective reviewer repeated a non-blocking claim that the dependency audit was missing. The versioned compatibility/license check above was already present in the staged evidence. This does not change its separate blocking POST finding.

## Owner decision

The owner answered: "승인할게. 목표는 단타 모의투자 시작이야." This explicitly approves the reviewed authentication-change commit exception and clarifies the operating goal as intraday paper trading. The automated BLOCK receipt is preserved. No live enablement or remote push is implied. The already-prepared legacy **intraday**, not daily, trial profile can be started under its separate reduced limits; the new intraday_v2 qualification gate remains in force.

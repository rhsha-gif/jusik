# paper-audit 2026-09-12 — A1 원본 보고 (실행 정확성·안전 불변식 반증)

에이전트: Claude Code `aorch-reviewer`, model `fable`(claude-fable-5-1), 독립 컨텍스트, 읽기 전용. 통합 보고서: `paper-audit-20260912.md`.

---

## 1. 한 줄 결론

**09-14 재개를 막는 결함 있음 (critical 0, high 2).** 실패 경로에서 취소 POST 실패가 보호 청산을 영구 봉쇄하고(A1-01), 결과 미상 주문에 종결 경로가 없어 미상 매도 1건이 커널 수준에서 이후 모든 보호 매도 claim을 거부한다(A1-02). 두 결함 모두 KIS 전송 오류(타임아웃, 5xx, TPS 초과) 한 번으로 촉발되며, 별도 프로세스 간 요청 예산 비공유(A1-04)가 촉발 확률을 높인다. 이중 제출 방지·상태기계·손실 한도 영속성 등 핵심 불변식은 성립한다(3절).

## 2. 발견 목록 (심각도 순)

### A1-01 · high · 수정 비용 standard — 취소 POST 실패 후 재시도 없음 → 해당 종목 보호 청산 영구 봉쇄

위치: `quantpilot\paper\broker.py:408-409, 436-446`, `quantpilot\paper\runtime.py:165-176`, `quantpilot\paper\risk.py:194-196`, `quantpilot\paper\store.py:15-22`

근거:
```python
# broker.py:408-409
if self.store.get("cancel_claim:" + order["id"]):
    return
# broker.py:436-446
with self.store.transaction():
    self.store.put("cancel_claim:" + order["id"], True)
    ...
    self.store.update_order(order["id"], "cancel_unknown", ...)
# A crash/timeout after the claim is query-only, never an automatic re-POST.
acknowledgement = self.client.cancel_paper_remaining_order(...)
```
```python
# runtime.py:169-171
except Exception as exc:
    self.alert("cancel_reconciliation_required", self.clock())
# risk.py:195
if not p or any(o["symbol"] == symbol for o in store.orders(True)):
    return 0
```
`cancel_claim:*`를 지우는 코드는 저장소 어디에도 없다(grep). `cancel_unknown`은 `OPEN`에 포함되므로 `sell_quantity`가 0을 돌려준다. `test_paper_daily_cancel.py:86-100`이 "타임아웃 후 재시작해도 `len(calls) == 1`"을 명시적으로 고정하고 있어 의도된 설계이나, 결과가 위험 감소 주문(취소)에는 부적절하다.

실패 시나리오: 손절 매도 지정가(bid) 발주 → 60초 경과로 `cancel()` → claim 기록 후 POST가 타임아웃/HTTP 5xx(토큰 만료·TPS 초과)로 `KisPaperCancelOutcomeUnknown` → 취소가 KIS에 도달하지 않은 경우 옛 bid 지정가 매도가 장중 내내 살아 있음 → 매 사이클 `cancel()`은 408에서 즉시 반환, `sell_quantity()`는 0 → 가격이 stop을 뚫고 내려가도 새 손절을 낼 수 없음 → `daily_halted`는 발동하지만 청산 불가 → `flatten`도 완료 불가(runtime.py:298-304), `pause`는 flattening 중 no-op(store.py:198-200). 해소는 KIS 행이 종결(체결 또는 장 종료 만료)될 때뿐이며 만료 행의 표현은 미검증(5절 #1).

최소 수정안: 취소는 위험 감소 동작이므로 "재대사 후 여전히 원주문 행이 `remaining_quantity > 0`이고 `orgn_odno == order_number`인 자식(취소) 행이 없음"이 증명되면 정확히 1회 추가 POST를 허용(횟수 상한 + 감사 기록). 대안: 자동 재시도를 금지하려면 운영자 명령(`recancel --order <id>`)과 인시던트 승격 중 하나는 반드시 제공.

회귀 테스트: `quantpilot\tests\unit\test_paper_daily_cancel.py` — `cancel_paper_remaining_order`가 TimeoutError 후, 일별 행이 여전히 working이고 자식 취소 행이 없을 때 `cancel()`이 정확히 1회 더 POST하는지; 반대로 자식 취소 행이 존재하면 POST 없음. `test_intraday_v2_risk.py` — `cancel_unknown` 매도가 남은 포지션에 대해 stop 돌파 시 새 보호 주문이 나가는지(현재는 나가지 않음).

### A1-02 · high · 수정 비용 standard — `outcome_unknown` 주문에 종결 경로 없음; 미상 매도 1건이 커널의 모든 신규 claim(보호 매도 포함)을 차단

위치: `quantpilot\packages\core\execution\paper_submission.py:207-220, 981-987`, `quantpilot\packages\core\kis_paper.py:214-215`, `quantpilot\packages\core\execution\paper_reconciliation.py:173-193`, `quantpilot\paper\broker.py:212-218`, `quantpilot\paper\risk.py:90-91`, `quantpilot\packages\db\sqlite_repositories.py:3351-3357`

근거:
```python
# kis_paper.py:214-215 — 4xx/5xx 구분 없이 전송 오류
except urllib.error.HTTPError as exc:
    raise KisPaperTransportError(f"KIS paper HTTP status {exc.code}") from None
# paper_submission.py:209-220 — 전송 오류는 전부 outcome unknown
except (KisPaperTransportError, KisPaperProtocolError, ...):
    raise KisPaperOrderOutcomeUnknown(...)
# paper_reconciliation.py:174,193 — 일치 행 0개면 그대로 반환
if len(candidates) == 0:
    ...
    return dispatch
# sqlite_repositories.py:3351-3357
if unresolved and (any(item.side == "sell" for item in unresolved) or not risk_reducing_sell):
    raise PaperStateConflictError("an unresolved paper dispatch blocks new external attempts")
```
`broker.py:212-218`은 `all(d["matched_rows"] == 1 ...)`를 요구하므로 미상 주문이 남아 있는 한 `entry_reconciled`는 영구 False, `risk.py:90`은 `outcome_unknown`이 하나라도 있으면 진입 0. 수동 종결 API는 `sqlite_repositories.py`, `paper\*.py`, CLI(`cli.py:93-121`) 어디에도 없다(grep `resolve|manual|acknowledge` 무결과). `expire_stale_prepared_dispatches`도 `outcome_unknown`은 그대로 둔다(paper_submission.py:981-987).

실패 시나리오: 주문 POST가 KIS 게이트웨이에서 거부(만료 토큰 EGW00123, TPS 초과 EGW00201)되어 HTTP 5xx → KIS OMS에는 주문이 없음 → 일별 조회 0건 → 영구 `outcome_unknown` → (a) 매수였다면 진입 영구 차단(보호 매도는 `risk_reducing_sell`로 허용), (b) **매도였다면 커널이 이후 모든 claim을 거부** → 다른 포지션의 보호 매도도 `PaperStateConflictError` → `position_protection_unavailable` 반복, 장중 무보호. 유일한 우회는 새 runtime-dir(새 원장)이며, 이는 손실 한도 상태·포지션 귀속을 모두 버린다.

최소 수정안: 운영자 명령 `resolve-unknown --order <id> --reason`(전제: paused, `local == remote` 잔고 검증, dispatch 시각 후 N분 경과, 최신 일별 조회 0건) → dispatch를 `rejected`로 종결(origin `operator_resolution`, 감사). 자동화가 필요하면 같은 전제를 코드로 검증한 뒤 `no_broker_evidence_after_window`로 종결하되 창은 분 단위 이상으로.

회귀 테스트: `quantpilot\tests\unit\test_paper_reconciliation.py` — 창 경과+잔고 불변+0건일 때만 종결, 조건 하나라도 빠지면 미상 유지; `test_paper_dispatch_persistence.py` — 미상 매도가 있을 때 보호 매도 claim이 거부됨을 명시적으로 기록하고 종결 후 허용되는지.

### A1-03 · standard · 수정 비용 low — 복구 경로로 `accepted`가 된 주문은 forwarding ID가 없어 취소가 조용히 영구 지연

위치: `paper_reconciliation.py:248-264`, `paper_submission.py:880-885`, `broker.py:427-435`, `quantpilot\paper\order_evidence.py:24-45`

근거: 대사기는 `broker_order_reference/branch/time`만 채우고 `broker_forwarding_order_org_number`는 POST 응답(`_record_acceptance`)에서만 설정된다. `cancel()`은 `not dispatch.broker_forwarding_order_org_number`이면 `cancel_deferred_to_reconciliation` 감사만 남기고 반환하며 인시던트도 없다.

실패 시나리오: POST 응답 유실(타임아웃) → `outcome_unknown` → 일별 행 일치로 `accepted` 복구 → 60초 후 매 사이클 `missing_forwarding_id`로 지연 → 지정가가 장중 내내 유효. 매수면 신호와 무관한 시점의 지연 체결로 포지션 생성, 매도면 A1-01과 같은 결과.

최소 수정안: 지연 N회 초과 시 `alert(...)`로 인시던트 승격. 아울러 `kis_paper.py:848-910`의 `cancel_full_remaining_order`는 `ord_gno_brno`를 `KRX_FWDG_ORD_ORGNO`로 그대로 쓰는데 `order_evidence.py:25`는 "distinct"라고 단언한다 — 같은 저장소 안에서 두 해석이 상충하므로 KIS 문서로 확정하고, 동일하다면 복구 케이스에 한해 일별 행의 branch 사용을 허용.

회귀 테스트: `test_paper_daily_cancel.py::test_missing_forwarding_id_never_uses_daily_branch`를 확장해 "N회 지연 후 인시던트" 또는 "복구 케이스 branch 취소"를 고정.

### A1-04 · standard · 수정 비용 low — 요청 예산이 프로세스 간 비공유 + 사이클당 중복 조회로 KIS TPS 상한에 근접

위치: `quantpilot\paper\cli.py:61-67`, `quantpilot\paper\intraday\collection.py:71-73`, `broker.py:118, 147, 162`, `runtime.py:114, 177`, `broker.py:364` vs `paper_submission.py:450`

근거: 트레이더와 수집기 스레드는 하나의 `LimitedTransport`를 공유하지만(cli.py:61, collector가 `runtime.market` 사용), 스트림/수집 프로세스는 `LimitedTransport(ReadinessTransport())`를 별도 생성(collection.py:71)하고 `minute_loop`에서 10초마다 최대 20종목 분봉을 조회한다. 트레이더 사이클: `reconcile()` 2회 × (대사기 `get_balance` + `get_balance` 재호출(147) + 일별 조회(162) [+ 대사기 일별 조회]) = 사이클당 잔고 4회·일별 2~3회, 매수 1건당 `get_buying_power` 2회. 두 프로세스 합산 ≈ 1.9 req/s.

실패 시나리오: TPS 초과 응답이 주문/취소 POST에 걸리면 A1-01/A1-02 촉발. 리미터 포화로 사이클이 늘어지면 `quote.as_of` 대비 15초 TTL 초과(`paper_submission.py:1090`)로 매수 prepare가 실패하고 `runtime.py:502-528`이 인시던트로 처리.

최소 수정안: `broker.py:147` 제거(`result.broker_balance` 재사용), `broker.py:364` 중복 매수가능 조회 제거 또는 결과를 `prepare_order`에 전달, 대사기의 일별 행을 162의 unmanaged 검사에 재사용. 프로세스 간 예산은 문서로 합산 상한을 명시하거나 파일 기반 리미터로 공유.

회귀 테스트: `test_intraday_durable_gateway.py` — 가짜 클라이언트로 `cycle()` 1회당 `get_balance`/`get_daily_orders_and_fills`/`get_buying_power` 호출 수 상한 고정.

### A1-05 · standard · 수정 비용 standard — 첫 `reconcile()` 예외가 그 사이클의 보호 단계를 전부 생략; 원장 불변식 위반은 결정적이라 영구

위치: `runtime.py:111-124, 185, 502-528`, `store.py:215-228, 291-306, 332-334, 357, 363-364`

근거: `reconcile()`(114)이 raise하면 `except`(502)로 가고 185의 보호 루프에 닿지 않는다. `reconcile()` 내부의 `verify_cash()`(store.py:215-228)와 `update_order`의 `nonmonotonic_fill`/`position_attribution_conflict`/`sell_exceeds_attributed_position`/`entry_cost_evidence_missing`은 결정적이므로 한 번 발생하면 매 사이클 동일하게 raise한다. `reconcile --apply`도 같은 `gateway.reconcile()`을 호출한다(recovery.py:189).

실패 시나리오(발생 확률 낮음, 영향 큼): 어떤 주문 1건의 대사가 불변식에 걸리면 나머지 포지션의 stop/target/time_limit 청산이 장중 내내 실행되지 않음.

최소 수정안: `broker.py:124-144` 루프에서 주문별 `ValueError`를 잡아 해당 주문을 인시던트로 표시하고 계속 진행; `verified_symbols`가 확보된 포지션의 보호는 유지.

회귀 테스트: `test_intraday_v2_risk.py` — 한 주문의 `update_order`가 raise해도 다른 보유 종목의 stop 청산이 나가는지.

### A1-06 · low · 수정 비용 low — 보유 종목 바 이력에 1분 결측이 있으면 `validate_bars` 예외 → 즉시 강제 청산

위치: `runtime.py:212-236`, `quantpilot\paper\strategy.py:142-146`, `quantpilot\paper\intraday\strategy.py:187`

근거: 보호 경로는 세션 전체 이력을 `protective_exit`에 넘기고, `validate_bars`는 `gap within trading session`을 raise → fallback `extra_exit = "strategy_protection_unavailable"`이 truthy라 248-268에서 매도 사유가 된다(`test_intraday_v2_risk.py:144-188`이 이 동작을 고정). 신호 경로는 362-370에서 연속 접미부로 갭을 허용하지만 보호 경로는 허용하지 않는다. 격리(revised) 바는 바 축적만 중단하고 갭을 만들지 않으므로 보호를 막지 않는다(질문 항목에 대한 답: 막히지 않음, 대신 결측 시 강제 청산). 설계 의도("unusable bars cannot disable liquidation")와 일치하나 수집기 30분 이상 중단이나 무거래 분에 청산이 촉발되는 점은 운영자가 알아야 한다.

### A1-07 · low · 수정 비용 low — 5분 lease가 사이클 중 갱신되지 않음

위치: `broker.py:67-69, 100-108`, `paper_submission.py:768-782`, `runtime.py:533-537`. `renew_paper_execution_session` 호출이 `quantpilot\paper`에 없다. 사이클이 5분을 넘기면(A1-04 포화 + 10초 타임아웃 누적 시 가능) POST 직전 fence 검증 실패로 정상 주문이 `paper_session_fence_invalid_after_claim`으로 거부되고 `end()`가 `execution_lease_close_failed` 인시던트를 낸다. fail-closed이므로 안전하나 노이즈.

### A1-08 · low · 수정 비용 low — `flattening` 상태에서 빠져나올 운영자 경로 없음

위치: `store.py:197-204`. `pause`는 no-op, `resume`는 raise. A1-01로 flatten이 정체되면 운영자가 상태를 바꿀 수 없다.

## 3. 확인했지만 문제 없던 불변식

- 이중 제출 방지 3중: `store.reserve` id 중복/종목 pending 거부(store.py:233-243) → `entry_size` pending 미상 시 0(risk.py:85-91) → 커널 CAS claim + 미해결 차단(sqlite_repositories.py:3315-3318, 3351-3357, 3372-3391). 재시작 시 `begin()`→`expire_stale_prepared_dispatches`가 prepared→`expired_pre_dispatch`, claimed→`outcome_unknown`(blocked)로 fence 처리(paper_submission.py:249-309), `_replay_without_post`가 재POST 금지(1005-1019). 로컬 prepared + dispatch 없음 → `expired_pre_dispatch`(broker.py:126-134).
- `update_order` 불변식(store.py:275-306): 누적 체결 재적용 시 delta 0으로 현금·포지션 불변, trades id `order:filled` 유일. 부분체결 후 취소 경쟁: dispatch 누적치를 그대로 적용하고 claim이 있으면 비종결 상태를 `cancel_unknown`으로 고정(broker.py:140-144), 확정은 KIS 행 종결에서만(paper_reconciliation.py:414-428, 431-474). `test_paper_daily_cancel.py` 부분체결+취소확인·지연체결 1회 적용 테스트 통과(44 passed, 4개 파일).
- `cancel()`은 일별 행 정확히 1개 + forwarding ID 필요, 아니면 무주장·무POST(broker.py:421-435, 파라미터 9종 테스트).
- 100페이지 캡 초과 → `KisPaperProtocolError`로 실패(조용히 불완전해지지 않음, kis_paper.py:1008-1034); 3개월 창 밖 dispatch는 `broker_history_window_manual_resolution_required`로 명시 blocked(paper_reconciliation.py:104-143); 창 밖 start는 ConfigurationError(kis_paper.py:638-641).
- `_parse_daily_order`: `cncl_cfrm_qty`/`cnc_cfrm_qty` 결측·상충 시 오류, 빈 `cncl_yn`은 정량 완전거부 증명 시만 N(1283-1318); `is_original_order` `0{1,16}`(1160-1166); `_request`의 order-cash 차단(1072-1075); `RecoveryTransport` 허용 목록(recovery.py:27-35); Strict transport 1회 시도·리다이렉트 거부·1MB(159-234).
- 손실 한도 영속: `intraday_loss_state`는 settings에 저장되고 `configure`가 건드리지 않음(store.py:124-164); `daily_halted`는 일 변경 시만 리셋(controls.py:40-45); `drawdown_halted`는 `review_intraday_halt`(사유 10자, paused/stopped, 무포지션·무주문)만 해제(store.py:166-186); 한도 필드 상한 `le=0.01/0.05` + 승인 정책 불변(config.py:22-23, store.py:131-140). `test_halts_survive_restart_and_policy_changes` 통과. 설계 메모: review는 `peak`를 현재 equity로 리셋한다(185).
- pause/flatten: paused → 미체결 매수 즉시 취소(runtime.py:146-150, 165-169), 매도 보호는 control과 무관(185-287), flatten 완료 조건(298-304). 시장가 없음: bid 지정가를 60초마다 재발주해 추적(167, 539-541, risk.py:40-47).
- KST 마감: `now >= closes` → 포지션 quarantined + postclose(127-142), 마감 20분 전 청산·30분 전 진입 차단(143-150), `authorize_order` 세션 밖 거부(risk.py:207-214), 커널이 claim 전후 세션 재확인(paper_submission.py:702-756). 마감 후에도 첫 reconcile(114)은 실행되어 동시호가 체결이 반영된다.
- 토큰: 90% 시점 갱신·60초 백오프·재생 없음(auth.py:21-35); coordinator는 begin 시점 클라이언트 고정(broker.py:71-81). 트레이더·수집기 스레드는 같은 리미터 공유(cli.py:61-72, collector.py 생성자).
- SQLite: Store WAL + `synchronous=FULL` + timeout 15(store.py:42-45), `BEGIN IMMEDIATE`(89-100), 스레드별 연결(collector.py:104), 커널 `busy_timeout 5000` + WAL(sqlite_repositories.py:379-388), 대시보드 `mode=ro` + `query_only`(dashboard.py:32-44). `configure(expected_version)`·`reserve`의 버전 검사가 IMMEDIATE 트랜잭션 안에서 수행(store.py:124-128, 233-239). 스트림 프로세스도 `Store`를 열어 `intraday_feed_at`을 쓰는 6번째 접근자이나 autocommit put이라 충돌 없음(collection.py:184-187).
- 복구: 4개 lock(recovery.py:157-169), paused·계정 바인딩·provenance 검사(98-107), 메모리 사본 + `broker_reconciliation` origin만 허용(74-95), 백업 후 적용(142-154, 178).

## 4. 읽지 않은 파일/범위

`quantpilot\paper\dashboard.py`(열기 부분 외), `reporting.py`(`drain_outbox`·`enqueue_recovery_report` 외), `intelligence.py`, `research.py`, `strategy.py`(`validate_bars`·`aggregate_five_minutes` 외), `intraday\stream.py`, `intraday\collection.py` 150행 이후, `intraday\data.py`, `sqlite_repositories.py`의 `update_paper_order_dispatch`/`takeover_prepared_paper_order_dispatch`/`reserve_and_insert_paper_order_dispatch` 본문, `position_ledger.py`, `jobs\check_paper_readiness.py`, `jobs\collect_intraday_data.py` 본문, `kis_paper.py` 300-480(데이터클래스). 범위 밖으로 지정된 Level-5 경로는 열지 않았다. 실제 원장·`~/.quantpilot`·환경변수 값은 열지 않았다.

## 5. 미해결 위험 (근거 부족)

1. 장 종료 시 KIS가 자동 만료시킨 미체결 주문의 일별 행 표현(`cncl_yn`/`rmn_qty`). 'Y'가 아니면 전날 주문이 영원히 `accepted`로 남아 해당 종목의 보호·진입이 차단되고, `cancel()`은 `business_date`가 오늘이라 항상 지연된다(broker.py:417-426).
2. 취소 요청의 자식 행(`orgn_odno == 원주문`)이 `rmn_qty > 0`로 나타나는지. 그렇다면 `is_original_order` False로 unmatched → `external_working_order`로 진입 차단(broker.py:171-180).
3. KIS 게이트웨이 거부(EGW00123, EGW00201)의 HTTP 상태. 5xx이면 A1-02 촉발; 200+`rt_cd≠0`이면 definitive rejection으로 안전.
4. 토큰 재발급 시 기존 토큰 반환 + `expires_in=86400` 응답 가능성. `auth.py:32-34`는 `expires_in`만 신뢰하고 `access_token_token_expired`를 파싱하지 않아 실제 만료보다 늦게 갱신할 수 있다.
5. `ord_gno_brno`와 `KRX_FWDG_ORD_ORGNO`의 동일성(A1-03).
6. 동시호가(15:20-15:30)에 `aspr_acpt_hour`가 갱신되는지. 갱신되지 않으면 `fresh_quote` 15초 TTL(risk.py:21) 때문에 마감 직전 청산이 불가하고 포지션이 격리 이월된다. 유동성 낮은 종목의 호가 정지도 같은 경로.
7. 런타임 venv의 `exchange_calendars` 설치 여부. 레포 `.venv`에는 없고(`ModuleNotFoundError`) `pyproject.toml:16`에는 선언되어 있다. 없으면 `Calendar()`에서 시작 자체가 실패한다(fail-closed).
8. XKRX 특별 개·폐장일(수능일 등) 처리 — 달력 라이브러리 의존.
9. 모의투자 계좌 리셋 시 `outside_cash_reserve`(broker.py:377-380)가 고정값이라 `broker_experiment_slice_exhausted`로 매수가 영구 차단될 수 있다(안전 방향이나 운영자 인지 필요).

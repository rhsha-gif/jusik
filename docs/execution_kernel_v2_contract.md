# Execution Kernel v2 Contract

Status: Codex contract hardening completed at `2de0965`; three independent
read-only audit axes report P0=0/P1=0. The Claude Code (`claude-fable-5`)
`QP-KER-000D` final counterpart review is committed on
`claude/qp-kernel-v2-final-review`: it independently reverified the contract
against current source, found P0=0/P1=2/P2=3, and closed all five inside this
document (version-comparison fallback, blocked-outcome parity normalization,
KIS Level 3 parity exclusion, AST-rule wording, review-branch diff base).
A subsequent independent Codex purity audit of that review found P0=0/P1=1/
P2=1: the "exact" AST purity rule still admitted mutable module globals
created by module-scope calls such as `sorted(...)`, and the
`strategy_versions_match` legacy rule was not total over Unicode digit
strings. Claude closed those findings in `0bbec72`. Three independent
post-fix audit axes consolidated to P0=0/P1=2/P2=4: the runtime scan rejected
normal Python import metadata and imported typing special forms, while
definition-time expressions in function defaults, annotations, decorators,
and class headers remained open; documentation also needed the complete
Unicode corpus, Gate-015 ownership/handoff, accurate audit evidence, and
Gate-070 flag-P2 tracking. Claude Code began a follow-up but reached its session limit
before producing a reviewed commit; that one-file partial worktree is
preserved. Codex hardening commit `6bfdb5d` closes those findings with the
exact definition-time grammar and runtime provenance rules below. Final
audits report authorization/version P0/P1/P2=0, purity/decision P0/P1/P2=0,
and KIS/durable P0/P1=0/P2=1 (the explicitly deferred Gate-070 flag/config
ADR only). The mission-lead integration commit `9fbf035` incorporates the
reviewed lineage, and `61bccac` records the safe handoff: docs-only scope,
mock broker, live disabled, and Level 5 blocked with zero submitted operator
orders. `QP-KER-010` may therefore begin, but no runtime implementation is
part of this contract gate. During `QP-KER-010`, an adversarial low-recursion
test demonstrated that the depth budget alone cannot contain recursive descent
when the validator has entered normally but the configured recursion limit is
reduced to 65.
`QP-KER-010A` is the docs-only safety correction below: it adds one narrowly
specified `RecursionError` containment form without permitting a broad catch,
raw error payload, retry, mutation, or new trading authority. This correction
must receive independent P0/P1=0 and Claude counterpart review before the
runtime slice can be accepted. The Claude Code (`claude-fable-5`)
`QP-KER-010B` counterpart review independently reproduced the reduced-limit
containment, the exact recursive-argument and handler forms, and the raw
value/path mutation rejections against the frozen runtime candidate; it found
P0=0/P1=0/P2=1 and closed the P2 here by binding the list/tuple recursive
variant into the reduced-headroom fixture corpus below.

## 1. Purpose and governing baseline

`QP-KERNEL-V2` turns the existing shared submission function into an explicit,
versioned execution contract without adding trading authority. The first slice
is a pure, read-only shadow evaluator. It does not submit, persist, reserve,
transition, reconcile, or repair anything.

This contract is subordinate to `AGENTS.md`,
`docs/agent_collaboration_protocol.md`, the accepted Gate 1 reservation
contract, and the accepted Gate 2 canonical-event contract. The governing
mainline is commit `70219d4`, which includes Gate 1 schema v10, Gate 2 schema
v11 shadow events, the Drift daily-snapshot correction, and the cross-feature
external-paper regression. The earlier `8eaf15a` contract base is historical
only.

The following invariants remain binding:

- `LIVE_TRADING_ENABLED=false`
- `GUARDED_AUTOPILOT_ENABLED=false`
- `FULLY_AUTOMATED_OPERATOR_ENABLED=false`
- `MARKET_ORDERS_ENABLED=false`
- `BROKER_MODE=mock`
- automatic tests use only fixtures and fake clients
- LLM/RL output cannot create, approve, or submit a broker order
- a claimed or otherwise ambiguous external POST is never automatically sent
  again
- schema-v10 reservations and authoritative dispatch rows remain authoritative
- schema-v11 execution events remain an append-only shadow journal
- account provenance, leases, and fencing remain mandatory for KIS paper
- kill, reconciliation, and risk-reducing work retain priority over new buys

## 2. Repository truth at the accepted baseline

### 2.1 Existing entry points

| Path | Entry point | Approval source | Common handoff |
|---|---|---|---|
| Level 1-2 fixture mock | `HarnessService.run_level_1_2_mock_execution()` | `_authorize_simulated_order_plan()` | `HarnessService.submit_order_plan()` |
| Level 3 direct | orders API: generate, approve, submit | `approve_order_plan()` / user approval | `HarnessService.submit_order_plan()` |
| Level 3 ticket | execution approval-ticket API | approved `TradeApprovalTicket` | `HarnessService.submit_order_plan()` |
| Level 4 | `HarnessService.run_guarded_autopilot_once()` | `authorize_level4()` | `HarnessService.submit_order_plan()` |
| Level 5 ordinary | `OperatorService.run_once()` -> `_submit_proposals()` | `authorize_level5()` | `HarnessService.submit_order_plan()` |
| Level 5 protective/retirement | professional cycle | Level 5 plus position-binding evidence | `HarnessService.submit_order_plan()` |
| KIS paper session | `run_kis_paper_session.build_runtime()` / `execute_runtime()` | Level 5/professional paths | the same harness handoff plus durable coordinator |

The submission path is already physically shared. What is not yet shared is a
typed authorization handoff and a separately testable execution-decision
contract. Level-specific services construct approval evidence, mutate the
`OrderPlan` to `user_approved`, and translate failures independently.

### 2.2 Current common submission responsibilities

`HarnessService.submit_order_plan()` currently performs all of the following:

1. loads in-memory order and policy state;
2. checks required explicit paper inputs;
3. checks order status and risk-check freshness;
4. performs a fresh single-order risk check;
5. performs submit-time batch risk evaluation;
6. captures final policy, kill, live, pause, and broker-health checks;
7. transitions the in-memory order to `submitted`;
8. for external paper, prepares the schema-v10 reservation and dispatch
   atomically;
9. calls exactly one broker adapter;
10. validates broker-order and fill evidence; and
11. updates in-memory projections and audit records.

Proposal-time and submit-time risk checks are deliberately both retained. The
second check is a time-of-check/time-of-use defence, not accidental duplicate
code.

### 2.3 Sole external-order authority

For KIS paper, the only normal order POST is:

```text
HarnessService.submit_order_plan
  -> DurablePaperSubmissionCoordinator.prepare_order
  -> KisPaperBrokerAdapter.submit_order
  -> DurablePaperSubmissionCoordinator.submit_prepared_order
  -> KisPaperClient.place_limit_cash_order
```

`DurablePaperSubmissionCoordinator` is the sole KIS order-POST authority. Its
prepared/claimed/outcome journal, reservation transaction, replay-without-POST,
and ambiguous-outcome rules cannot be copied into Kernel v2. Cancel authority
belongs to the existing paper-kill path and is outside this mission.

`MockBroker` and the non-external `PaperBroker` are deterministic simulators.
They remain side-effect owners for their current paths until their individual
cutover gates are accepted.

### 2.4 Important existing limitations

- `ExecutionKernel`, `AuthorizationDecision`, `ExecutionContext`, and a broker
  capability model do not yet exist.
- `AuthorityCheckResult` is the current Level 4/5 check result but does not by
  itself bind a human actor or approval ticket.
- The direct Level 3 approval endpoint currently accepts no approver identity
  and `approve_order_plan()` does not set `OrderPlan.approved_by`. It proves a
  local approval transition and audit source, not an authenticated human
  signature.
- `run_risk_check()`, `authorize_level4()`, and `authorize_level5()` read
  environment-derived feature state. They therefore cannot be called from the
  pure kernel unless those reads are first moved behind an adapter.
- schema-v10 reservation is implemented only for external KIS paper. A shadow
  result must not claim that mock or simulated-paper paths have a durable
  reservation.
- schema-v11 execution events shadow external paper dispatch/reservation/cancel
  aggregates. They are not a general OMS ledger.
- `partial_allow` is still a boolean at several API and risk boundaries. Its
  portfolio semantics are a later, separate change.
- The current `submit_order_plan()` checks `risk_check_expires_at` but does not
  explicitly reject the current order's own `OrderPlan.expires_at`. The batch
  helper filters expired repository candidates, then unconditionally
  re-appends the current order, so it does not protect that order. This is a known
  fail-closed hardening delta, not parity evidence to normalize away. It was
  reproduced in-memory: a user-approved mock order with past
  `OrderPlan.expires_at` and still-fresh risk evidence reached `filled`. This is
  a P1 pre-cutover finding.

## 3. Bounded mission outcome

The first accepted runtime outcome is:

> Given a complete immutable evidence bundle, Kernel v2 returns the same
> allow/block execution intent deterministically, while being mechanically
> incapable of observing or changing external state.

The first slice does **not**:

- replace `HarnessService.submit_order_plan()`;
- change any Level 1-5 authorization decision;
- create or submit a broker order;
- call a broker, KIS client, coordinator, repository, audit recorder, event
  store, clock, environment reader, or random/ID generator;
- prepare, claim, release, or consume a risk reservation;
- write canonical events or repair authoritative rows;
- add a transactional outbox, worker, accounting ledger, or continuous loop;
- redesign `partial_allow`;
- add live, market-order, flatten, multi-account, or multi-broker capability;
- change API or frontend contracts; or
- validate real KIS behavior.

## 4. Pure domain boundary

### 4.1 Module boundary

The first implementation owns only:

```text
quantpilot/packages/core/execution/kernel.py
quantpilot/tests/unit/test_execution_kernel_v2.py
```

Purity is enforced with an exact import-form/member allowlist, not a root-only
allowlist or denylist. `kernel.py` may contain only these unaliased imports:

```python
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, ValidationError
```

No alias, star import, other member, or alternate form is equivalent. In
particular `from decimal import DefaultContext`, `from json import
_default_encoder`, importing mutable registries/context, and importing
`hashlib`/`json` members directly are rejected even though their roots are
listed. The runtime identity check applies to exactly these bindings.

It has no project-internal imports in v1. In particular, importing
`core.schemas` would transitively expose wall-clock/ID factories and is not
allowed. An AST test rejects every non-allowlisted import, `open`,
`__import__`, `eval`, `exec`, dynamic import/reflection, filesystem, socket,
subprocess, environment, random/secret/UUID generation, wall-clock calls, and
mutable module globals. The contract does not claim transitive purity for any
future import until that module receives its own equivalent audit.

The purity checker parses `kernel.py` before import and builds four exact name
sets: allowlisted import bindings, immutable module constants, module
functions, and module classes. A name may occur in only one set; imported
names may not be rebound, shadowed at module scope, or deleted. The policy is
a closed allowlist of statement and expression forms, not a call-name
denylist:

- Every `Import`/`ImportFrom` must equal one row of the exact form/member table
  above. Imports may occur only as direct module statements.
  `from __future__` is not needed and is forbidden. Every `Load`, not merely
  calls, of `open`, `__import__`, `eval`, `exec`, `compile`, `getattr`,
  `setattr`, `delattr`, `globals`, `locals`, `vars`, `print`, `input`,
  `breakpoint`, `exit`, `quit`, `help`, `SystemExit`, `KeyboardInterrupt`,
  `GeneratorExit`, `BaseException`, or `__builtins__` is forbidden anywhere;
  every `Lambda` is forbidden; attribute calls ending in `now`, `utcnow`, or
  `today` are also forbidden.
  Import aliases are resolved before these checks.
- `Global` and `Nonlocal`, async definitions, `await`, and attribute or
  subscript assignment/deletion are forbidden anywhere. Direct calls must resolve to a
  module function/class already accepted by this checker or to the closed
  pure built-in set `all`, `any`, `enumerate`, `isinstance`, `issubclass`,
  `len`, `max`, `min`, `range`, `sorted`, `str`, `int`, `bool`,
  `bytes`, `tuple`, `type`, and `zip`. Imported-module calls are limited to
  `json.dumps` and `hashlib.sha256`; `json.load`/`dump`, `print`, `input`,
  `breakpoint`, process termination, and any call through a parameter or data
  field are forbidden. The complete attribute-method table is:
  `str.{strip,split,isdigit,encode,replace,join}`, `dict.items` on a
  function-local canonicalization mapping only,
  `Decimal.{as_tuple,is_finite}`, `datetime.{utcoffset,astimezone,isoformat}`,
  `BaseModel.{model_validate,model_dump}`, `ValidationError.errors`, and
  `sha256.hexdigest` on the function-local hash returned by the sole approved
  hash call. Receivers must be the statically known imported/local class,
  typed frozen-model/scalar parameter, or a local value produced by an
  approved call; arbitrary parameters and evidence fields are never invoked.
  No mutation method (`append`, `extend`, `update`, `setdefault`, item
  assignment, and similar), file/socket/process method, or unknown receiver
  method is allowed. Expanding either call table requires a reviewed contract
  change.
- Approved callable names and roots (`json`, `hashlib`, every safe built-in,
  every module function/class, and every imported binding) are reserved: they
  cannot be rebound or used as a parameter, comprehension/loop target,
  exception target, or local assignment in any scope. Direct-call resolution
  uses lexical scope, so a shadowed name never inherits allowlist status. No
  call permits `*args` or `**kwargs`, and a function/class/module/callable name
  may not be supplied as runtime data. Class/type names may appear only as a
  direct `Call.func`, in the closed type-annotation and class-base grammars,
  as the two exact exception-handler types below, as the second operand of
  `isinstance`/`issubclass`, or as the right-hand operand of the raw copier's exact
  `value_type is ApprovedClass` / `value_type in (ApprovedClass, ...)`
  classification. An approved callable attribute may occur only as
  the `func` of its approved `Call`; it cannot be loaded and passed as data.
  Attribute loads rooted at an imported module/class are otherwise rejected
  except immutable value access explicitly needed by the type/value grammar
  (`timezone.utc` and declared frozen-model scalar fields).
- The call signatures are closed. `all`/`any`/`len`/`tuple`/`type` take one
  positional argument; `enumerate` takes one or two; `range` takes one to
  three; `zip` takes positional iterables only; `sorted` takes exactly one
  positional iterable and **no** `key`/`reverse`; `min`/`max` take positional
  values only and no keywords; scalar constructors take at most one
  positional value. `isinstance`/`issubclass` take two positional arguments
  and their type operand is an approved class or literal tuple of approved
  classes. `json.dumps` receives only the detached canonical local tree and
  the constant keywords `ensure_ascii`, `allow_nan`, `sort_keys`, and
  `separators`; `default` is forbidden. `hashlib.sha256` receives exactly one
  local bytes value. Every allowed method has only the following exact
  arguments: `strip()`, `split(".")`, `isdigit()`,
  `encode("utf-8")`, `replace("+00:00", "Z")`, `join(local_strings)`,
  `items()`, `as_tuple()`, `is_finite()`, `utcoffset()`,
  `astimezone(timezone.utc)`, `hexdigest()`, `model_validate(detached_tree)`,
  `model_dump(mode="python")`,
  `errors(include_url=False, include_context=False, include_input=False)`,
  and `isoformat(timespec="microseconds")`. No omitted optional argument is
  implicitly accepted; in particular zero-argument `astimezone()` is forbidden
  because it reads the host local timezone. Thus constructs such
  as `sorted(values, key=open)` or `json.dumps(value, default=eval)` are AST
  violations even before the forbidden callable-name loads are considered.
- Function bodies use a closed statement grammar: optional docstring, simple
  local assignment/unpacking, `if`, finite `for` over an approved typed/local
  iterable, `return`, and only the three exact `try` forms required by v1.
  `validate_kernel_input_v1` may catch imported `ValidationError`.
  Only `validate_kernel_input_v1` may raise
  `KernelEvidenceValidationError`, built once from the fully aggregated,
  sanitized local code/path tuple with the cause expression exactly `None` so
  a rejected raw or Pydantic validation exception cannot leak through chained
  traceback. The version helper may catch only `ValueError` and return
  mismatch. The third form exists only inside `_copy_raw_value`: a `try` body
  contains exactly one tuple assignment from its sole recursive
  `_copy_raw_value(...)` call. The dict variant's six arguments are exactly
  `(raw_item, child_path, raw_key, depth + 1, remaining_budget,
  child_ancestors)`; the list/tuple variant's are exactly
  `(raw_item, path + "[]", field_name, depth + 1, remaining_budget,
  child_ancestors)`. No constant, reset budget, alternate path, or ancestor
  substitution is equivalent. One unnamed `except RecursionError` handler
  assigns exactly `copied_item = None`, `remaining_budget = 0`, and one
  sanitized finding. In the dict branch that finding path is the already
  allowlisted local `child_path`; in the list/tuple branch it is exactly
  `path + "[]"`. The handler may not read `value`, `raw_item`, an exception
  object, or any other raw value; may not call, format, stringify, or hash;
  and has no `else`/`finally`. The other two forms likewise have no
  `else`/`finally` or broad handler. `_copy_raw_value` never leaks a recursion
  exception, and no other raise/cause/handler form is allowed. Every
  other `Raise`, `Assert`, `Yield`/`YieldFrom`, `With`, `While`, `Match`,
  function-local import, nested function/class, async construct, `break`/
  `continue`, deletion, or augmented assignment is forbidden. Assignment
  targets are local simple names/tuples only; attribute/subscript stores remain
  forbidden.
- Runtime expressions are also closed. They may contain `Constant`, approved
  lexical `Name`, tuple/dict displays, `BoolOp`, `IfExp`, `UnaryOp` (`not`,
  unary signs on exact int only), arithmetic/string `BinOp`
  (`+`, `-`, `*`, `//`, `%`),
  primitive/identity/membership `Compare`, an approved `Call`, a
  provenance-approved `Attribute`/`Subscript`, and tuple/dict/generator
  comprehensions whose targets are simple locals and whose iterables already
  have exact built-in or frozen-tuple provenance. List/set displays,
  `ListComp`/`SetComp`, f-strings/formatting, lambdas, walrus, starred values,
  power/matrix/bitwise runtime operators, and every unlisted expression node
  are forbidden. Arithmetic may consume only exact `int` and
  string/tuple values. Finite `Decimal` and datetime values permit only
  equality/order/identity comparisons plus their approved context-free
  methods; Decimal unary signs and arithmetic are forbidden because they can consult and mutate
  process-global decimal context flags/precision. No operation is attempted
  on an unclassified raw value.
- Attribute loads are permitted only for: a declared field reached through a
  statically typed `FrozenKernelModel`; the exact immutable constant
  `timezone.utc`; the `tzinfo` slot inside the dominated exact-datetime raw
  branch; or the receiver/method pair of the closed
  call table. Subscript loads are permitted only on exact local dict/list/
  tuple/string values, frozen tuple fields, or inside the exact raw-copier
  branch below. Imported module/class attributes, function/class metadata,
  dunder attributes, and mutable external registries/context (including
  `decimal.DefaultContext`, `hashlib.algorithms_available`, and
  `json.encoder`) are rejected. The checker propagates model-field and
  approved-call result provenance through simple local assignments; unknown
  provenance fails closed.
- The validation boundary consists only of `validate_kernel_input_v1` and one
  private recursive `_copy_raw_value` helper; these are the only functions
  that accept an unvalidated object. Before Pydantic sees it, the preflight
  requires an exact built-in
  `dict` root with exact string keys and recursively accepts only exact
  `dict`/`list`/`tuple`, `None`, `bool`, `int`, `str`, `bytes`,
  `Decimal`, and aware `datetime` values whose `tzinfo` has exact built-in
  `timezone` type. It copies dicts, normalizes exact
  lists/tuples to new tuples, and never calls, formats, stringifies, or takes
  `repr` of a supplied value. Subclasses, callables, custom mappings/iterables,
  naive/non-finite values, or unknown objects append a sanitized structural
  finding containing only an allowlisted field path/code. The helper returns
  `(detached_or_safe_none, remaining_budget, findings_tuple)`, substitutes
  exact `None` for an unsafe/over-limit branch, and continues safe siblings so
  classification never depends on traversal order. The safe detached
  exact-built-in tree is always passed to
  `KernelEvaluationInputV1.model_validate` (or `{}` when the root itself was
  invalid) so type/extra/missing defects in other branches are still found;
  no raw value may reach `json.dumps`, hashing, an error formatter, or another
  local function. In `_copy_raw_value`, the first operation is
  `value_type = type(value)`. Any iteration, subscript, comparison other than
  that identity classification, or method call on `value` must be lexically
  dominated by the matching `value_type is dict/list/tuple/str/bytes/int/bool/
  Decimal/datetime` branch. The checker has a dedicated rule for this
  helper; besides the exact safe built-ins/methods above, the only module
  function it may call is itself. This explicit container normalization is the sole
  pre-model coercion; scalar values remain strict.
- Inside the exact-datetime branch, the helper first loads only the C-level
  `value.tzinfo` slot, requires `type(value.tzinfo) is timezone`, and only then
  may call `utcoffset`/`astimezone`. A custom `tzinfo` subclass is rejected
  without invoking it. Fixed-offset built-in `timezone` values remain valid,
  preserving the equal-instant/different-offset fingerprint case.
- Within the supported validation runtime, preflight is total over cyclic,
  deeply nested, and oversized exact containers. The support premise is that
  `validate_kernel_input_v1` and the first `_copy_raw_value` invocation have
  entered and the interpreter retains enough frames to run the fixed
  non-recursive handler and sanitized validation-error path. The binding
  reduced-limit regression is `sys.setrecursionlimit(65)` from the focused
  test stack. V1 makes no claim when Python cannot enter that boundary or
  construct its exception because the process is already at the absolute
  recursion limit. Immutable
  module constants bind `MAX_RAW_DEPTH=64`, `MAX_RAW_NODES=50000`,
  `MAX_RAW_CONTAINER_ITEMS=10000`, `MAX_RAW_TEXT_CHARS=65536`, and
  `MAX_RAW_BYTES=65536`, `MAX_RAW_ABS_INT=9223372036854775807`,
  `MAX_DECIMAL_DIGITS=128`, and `MAX_DECIMAL_ABS_EXPONENT=128`.
  `_copy_raw_value` carries depth and remaining-node budget as integers and
  returns the detached value, remaining budget, and immutable finding tuple;
  no mutable seen-set is
  required. It checks depth/budget before descent and container/text/bytes size only
  after the exact-type branch. Ancestor identity detects cycles before
  recursive descent; ordinary deep inputs reach the depth guard; and if the
  interpreter raises earlier because remaining stack headroom is smaller,
  the exact recursive-call handler above records the same sanitized structural
  family and returns through safe ancestors. Under the stated premise, no
  recursive-descent `RecursionError` or raw path object escapes. Exact ints
  are range-checked before later use. Exact finite
  Decimals are checked via `as_tuple()` for digit count and exponent range,
  without Decimal arithmetic or global context. Depth, node, item, text,
  numeric-range, naive-time, and non-finite failures
  use closed codes/allowlisted paths and never embed raw keys or values.
- Direct module statements may only be one optional docstring, allowlisted
  imports, class/function definitions satisfying the definition grammar
  below, and plain `Assign` statements from the immutable-constant grammar to
  one simple name. Module `AnnAssign`, type-alias assignments, executable
  expressions, control flow, augmented assignment, and deletion are all
  forbidden. PEP-604 unions and `Literal` expressions are written inline in class
  field or function annotations; no typing object or Pydantic field metadata
  is stored in a module-global alias.
- The immutable-constant grammar contains only `None`/`True`/`False`, string,
  bytes and integer literals; unary `+`/`-` on integer
  literals; tuple displays whose elements recursively use this grammar; and
  previously declared immutable module constants. It excludes every call,
  attribute, subscript, comprehension, generator, lambda, f-string, starred
  expression, named expression, binary operator other than the unary numeric
  signs, and list/set/dict display. Therefore `CHECKS = sorted(...)`,
  `json.loads(...)`, `tuple(...)`, `frozenset(...)`, `model_dump()`, and all
  other module-scope calls are rejected.

Every expression evaluated while a `def` or `class` is created is separately
closed:

- A function may omit an annotation. When present, each parameter and return
  annotation must use the type-expression grammar below. Positional and
  keyword defaults are absent or use the immutable-constant grammar; no
  call, mutable display, `*`/`**` expansion, or default factory is legal.
  Module functions have no decorators. Kernel evidence classes define no
  methods, so class-body function definitions and every decorator are
  forbidden in v1. Timestamp awareness, bounded strings/numbers, and
  cross-field rules are checked by the pure validation/evaluation functions,
  not by definition-time Pydantic decorators.
- The type-expression grammar permits: built-in scalar names `str`, `int`,
  `bool`, and `bytes`; `None`; frozen model classes already defined in the
  module; and the exact local names `datetime`, `timezone`, `Decimal`, and
  `Literal` from the import table.
  `BaseModel` is permitted only in the exact class-base shape below; imported
  functions such as Pydantic helpers, `json` functions, and hash functions
  are never type operands. The grammar also permits PEP 604 `A | B` and
  subscripts of only `tuple` and `Literal`. Generic tuple slices and
  `Literal[...]` accept only recursively valid type expressions or literal
  constants as appropriate. Attribute access, calls, all other subscripts,
  and other operators are rejected. The recursive model-tree test remains the
  stricter gate that forbids `Any`, `object`, `dict`, `list`, `set`, and open
  mappings in persisted evidence models.
- Class headers have no decorators, keywords, or metaclass expression. Their
  bases must be simple names and one of three exact shapes:
  `FrozenKernelModel(BaseModel)`, an evidence model with the sole base
  `FrozenKernelModel`, or the one public error
  type has the exact shape `KernelEvidenceValidationError(ValueError)`. A
  class body permits
  a docstring; annotated Pydantic fields whose annotation uses the type
  grammar and whose optional default is
  an immutable constant; exactly one
  `model_config = ConfigDict(...)` in `FrozenKernelModel` with constant
  keyword arguments from the exact set `frozen`, `extra`, `strict`, and
  `revalidate_instances`. No positional argument, nested call, method, or
  other class-body statement/definition-time expression is legal.

The runtime companion gate is a backstop driven by those AST sets; it does not
guess provenance from value type:

- It exempts only the exact interpreter-created names `__name__`, `__doc__`,
  `__package__`, `__loader__`, `__spec__`, `__file__`, `__cached__`, and
  `__builtins__`, after checking their source-derived exact values and that
  source code never loads or assigns them. Name, docstring, package, source
  path, and cache path must equal the AST/module path values; `__spec__` has
  exact `ModuleSpec` type with matching name/parent/origin; and `__loader__`
  must be identical to `__spec__.loader`. `__builtins__` must be the actual `builtins` module or an
  exact dict whose entries for **every** permitted built-in name loaded by the
  valid source (calls, raw type comparisons such as `dict`/`list`, class bases
  such as `ValueError`, and exception handlers) are identical to the
  corresponding object in the actual `builtins` module. Invalid source that
  loads a forbidden name is rejected by the pre-import AST gate instead.
  No other dunder
  name is automatically exempt. `__annotations__` must be absent because
  module `AnnAssign` is forbidden.
- Each AST import binding is exempt only when resolving the allowlisted source
  module/member in the test process yields the identical runtime object. This
  covers the imported typing special form `Literal` without
  exempting a rebound value.
- Each AST class/function name must resolve to a class/function respectively.
  Function `__defaults__` must be `None` or a tuple exactly equal to the AST
  positional-default constants and recursively satisfying the immutable-
  constant runtime types. `__kwdefaults__` must be `None` or an exact dict
  whose string-key/value pairs equal the AST keyword-only defaults and whose
  values recursively satisfy the immutable-constant types;
  this narrowly allowed interpreter-created dict is not a general mutable-
  global exemption. Every remaining runtime name must be
  an AST-declared constant whose value is `None`, `bool`, `int`, `str`,
  `bytes`, or a tuple recursively containing only those
  values. Any additional name or any list/set/dict/bytearray/open-ended value
  fails. Class metadata is trusted only because the exhaustive class-header,
  field, default, decorator, and body AST grammar above has already passed.

Binding regression fixtures prove the checker rejects module
`CHECKS = sorted(("b", "a"))`, `def f(cache=json.loads("[]"))`, a call in an
annotation, a call in a class base, an arbitrary decorator call, a module
`AnnAssign`, an imported-name rebind, an injected mutable runtime global,
`sorted(values, key=open)`, `json.dumps(value, default=eval)`, a `SystemExit`
raise, `from decimal import DefaultContext`, `from json import
_default_encoder`, an unapproved imported attribute read, and a tampered
`__builtins__` binding. Raw validation fixtures include objects whose `__iter__`,
`__getitem__`, comparison, `__str__`, `__repr__`, `__format__`, and `__call__`
all explode, plus an exact `datetime` carrying a hostile custom `tzinfo`;
none is invoked and no attacker text appears. Self-referential dict/list,
70-level acyclic input under deliberately reduced recursion headroom — in
both the exact-dict recursive variant and the exact-list/tuple recursive
variant, under the same `sys.setrecursionlimit(65)` premise —
depth/node/item/text overflow, and naive/non-finite values all
return the same sanitized structural-error family without an uncaught
exception. A source-mutation fixture replacing the recursion handler's
allowlisted path with raw `value` must fail the AST gate. Two binding
aggregation fixtures require: two naive timestamps
produce `naive_or_invalid_timestamp` with sorted allowlisted paths, while one
naive timestamp plus any non-timestamp defect produces
`invalid_evidence_schema`; preflight may not stop at the first naive field.
A minimal kernel-shaped fixture using frozen Pydantic fields, inline PEP-604
union/`Literal`/tuple annotations, and the exact `ConfigDict` must pass both
AST and runtime gates. The reduced import allowlist is exactly the list above.

All closed categorical/state/check fields in v1 use inline
`Literal["..."]` annotations, never Python `Enum` fields. This is binding
because Pydantic `strict=True` rejects a raw string for an Enum-typed field,
while accepting an Enum instance would expand the raw-object boundary. Literal
strings keep the raw adapter, strict structural validation, canonical JSON,
and fingerprint representation identical.

### 4.2 Versioned input

`KernelEvaluationInputV1` is a deeply immutable value object. It contains
primitive frozen projections of current authoritative objects; it is not a new
persisted order schema. It must not embed a mutable `HarnessModel` instance.
Every model inherits one `FrozenKernelModel` configured with
`frozen=True`, `extra="forbid"`, and `strict=True`. Collections are tuples of
closed frozen element types. `Any`, `object`, `dict`, `list`, and `set` are
forbidden anywhere in the model tree. Opaque references are bounded,
non-empty strings; fencing tokens/revisions are strict non-negative integers.

Required fields:

```text
schema_version = 1
observation_phase:
  authorization_failure
  external_paper_input_failure
  candidate_failure
  single_risk_failure
  batch_risk_failure
  final_safety_failure
  capability_failure
  ready_to_submit
candidate: KernelOrderCandidateV1
authorization: AuthorizationEvidenceV1
single_risk: SingleRiskEvidenceV1
batch_risk: BatchRiskEvidenceV1
final_safety: FinalSafetyEvidenceV1
context: ExecutionContextSnapshotV1
paper: PaperSubmissionEvidenceV1
capability: CapabilityEvidenceV1
evaluated_at: aware datetime
```

Each stage evidence carries an evaluation state:

```text
passed | failed | not_evaluated
```

Paper evidence also permits `not_applicable` for non-external profiles. The
states follow the exact route-specific prefixes below. An adapter may never
synthesize a result for a gate that did not run.

| Observation phase | Required prefix in actual legacy order |
|---|---|
| `authorization_failure` | authorization=failed; paper/single/batch/final/capability=not_evaluated; pre-authorization candidate state is allowed |
| `external_paper_input_failure` | authorization=passed; paper=failed; single/batch/final/capability=not_evaluated |
| `candidate_failure` | authorization=passed; paper=passed or not_applicable; candidate fails; later stages=not_evaluated |
| `single_risk_failure` | authorization and paper/candidate passed; single=failed; batch/final/capability=not_evaluated |
| `batch_risk_failure` | preceding stages passed; single=passed; batch=failed; final/capability=not_evaluated |
| `final_safety_failure` | preceding stages passed; final=failed; capability=not_evaluated |
| `capability_failure` | all preceding stages passed; capability=failed |
| `ready_to_submit` | authorization, paper-or-N/A, candidate, single, batch, final, and capability all passed |

Any impossible prefix blocks with `evidence_prefix_mismatch`. The pure model
does not create a broker-capability or KIS-provenance snapshot when its stage is
`not_evaluated`. `user_approved` is required only from
`external_paper_input_failure` onward; it is not allowed to hide the real
authorization failure while the legacy plan is still pre-approval.

Every nested value is copied at the adapter boundary and is JSON serializable
with canonical ordering. A recursive model test rejects mutable or open-ended
field types, and a source-container mutation test proves that later changes to
the raw adapter input cannot change the validated input, decision, or
fingerprint.

#### `KernelOrderCandidateV1`

This is a read-only projection of an existing `OrderPlan`. The mutable
`OrderIntent` model is converted to `KernelIntentSnapshotV1` rather than
embedded directly:

```text
order_plan_id
intent: KernelIntentSnapshotV1
policy_id
policy_version
purpose
status
idempotency_key
risk_check_id
risk_check_expires_at
approved_by
order_expires_at
strategy_binding: optional KernelStrategyBindingV1
```

`KernelIntentSnapshotV1` contains the existing intent ID, normalized symbol,
side, order type, quantity, limit price, notional, target weight, reason, and
aware quote time as scalar values. Numeric source values are converted with
`Decimal(str(value))`, must be finite, and serialize as canonical decimal
strings; the evaluator does not perform portfolio arithmetic on them. It must
retain the existing IDs rather than generate new ones. The adapter validates
that the intent, policy identity, and idempotency key came from one `OrderPlan`
snapshot.

`KernelStrategyBindingV1` is projected from `OrderPlan.explanation`:

```text
strategy_id
strategy_version
symbol
side
policy_version
```

The adapter rejects a binding whose symbol, side, or policy version differs
from the candidate. External KIS paper requires this binding because
`prepare_order()` already requires matching explanation evidence.

#### `AuthorizationEvidenceV1`

This is a `kind`-discriminated union. Each variant has the same closed common
envelope and a typed variant payload; payload fields may be absent only where
absence must become a semantic block rather than a schema exception.

```text
common envelope:
  kind:
    simulated_level_1_2
    human_direct_level_3
    human_ticket_level_3
    guarded_level_4
    automated_level_5
    professional_risk_reduction
  evaluation_state: passed | failed | not_evaluated
  authority_algorithm_version
  source:
    simulated_harness | level3_direct_transition | level3_ticket |
    guarded_authority_v1 | level5_authority_v1 |
    professional_authority_v1
  authorized: bool | none
  policy_id
  policy_version
  policy_user_id
  assurance:
    simulated | unverified_local | authenticated_subject |
    policy_authorized | operator_authorized
  evaluated_at: aware datetime
  checks: ordered tuple of {name, passed, detail_code}
  first_failed_check: optional closed check name
```

The evaluator, not Pydantic cross-field validation, enforces variant-required
and variant-forbidden payload fields. Missing, extra-for-kind, or mixed-kind
evidence returns `authorization_evidence_mismatch` rather than changing the
structural-error boundary.

Level 1-3 payloads and algorithm-v1 check sequences are fixed here:

```text
SimulatedLevel12EvidenceV1:
  source=simulated_harness
  assurance=simulated
  payload: simulation_reference
  checks:
    simulated_execution_only
    mock_profile_required

DirectLevel3EvidenceV1:
  source=level3_direct_transition
  assurance=unverified_local
  payload:
    approval_transition_source
    approval_transition_at
    authenticated_subject_id=None
    authentication_reference=None
  checks:
    local_approval_transition_recorded
    order_state_approved

TicketLevel3EvidenceV1:
  source=level3_ticket
  assurance=unverified_local unless a later authenticated-subject gate lands
  checks:
    ticket_status_approved
    ticket_time_valid
    ticket_identity_match
    ticket_data_mode_match
    order_state_approved
```

The direct payload rejects any non-null authenticated subject/reference at the
current baseline. Ticket authentication assurance is one of
`none | caller_label_only | authenticated_session`; only the last may support a
future authenticated-subject gate, which is not part of v1. Candidate
`approved_by`, policy user ID, and caller labels never substitute for it.

Level 3 ticket payload is explicit:

```text
ticket_id
ticket_user_id
ticket_policy_id
ticket_policy_version
ticket_order_plan_id
ticket_data_mode
ticket_status
requested_at
approved_at
expires_at
approved_by_label
authenticated_subject_id: optional
authentication_reference: optional
authentication_assurance: optional
```

`approved_by_label` is an unauthenticated caller label. It never becomes
`actor_id` or authenticated assurance. `ticket_status=approved` and
`approved_at <= evaluated_at < expires_at` are mandatory, and every ticket
identity must equal the candidate, policy, user, and context identity. At the
current v1 baseline direct and ticket Level 3 may use mock or the explicitly
listed in-process simulated-paper compositions. KIS paper Level 3 remains
blocked until a separate authenticated-subject binding mission lands.

Level 4 payload contains the frozen recipe authority:

```text
recipe_strategy_id
recipe_version
promotion_status
allowed_execution_levels: tuple
```

Level 5 payload contains:

```text
operator_run_id
recipe_strategy_id
recipe_version
registry_strategy_id
registry_version
registry_spec_hash
registry_status
registry_allowed_execution_levels: tuple
registry_min_policy_version
registry_max_policy_version
lifecycle_strategy_id
lifecycle_version
lifecycle_status
lifecycle_spec_hash
```

Candidate explanation, recipe, registry, and lifecycle ID/version/hash values
must agree. Level 4 requires promotion `approved` or `validated_l4` and the
Level 4 execution marker. Ordinary Level 5 requires `validated_l5`, an allowed
Level 5 marker, and a policy version within the registry range. `revoked` is
always blocked. `disabled` may be considered only by the separately gated
professional reduce-only variant.

The evaluator recomputes lifecycle binding; it does not trust a supplied
`binding_decision` or `required_status`. Registry status earns levels exactly
as follows:

```text
draft: {}
validated_l3: {level_3}
validated_l4: {level_3, level_4, guarded_autopilot}
validated_l5: {level_3, level_4, guarded_autopilot, level_5, fully_automated}
disabled: {}
revoked: {}
```

Authority levels are `allowed_execution_levels ∩ earned_levels`. Their minimum
lifecycle is: research-only -> none; Level 3 -> `backtested`; any paper, Level
4, or Level 5 marker -> `paper_validated`; any live marker or unknown non-empty
marker -> `live_candidate`. Lifecycle rank is
`draft < backtested < paper_candidate < paper_validated < live_candidate`;
disabled/revoked fail. Registry and lifecycle strategy ID/version/spec hash
must match, with exact string equality for registry-to-lifecycle versions.
Recipe-to-registry and ordinary candidate-explanation-to-recipe versions use
the legacy `strategy_versions_match` rule made total. The decision-complete
rule is: trim whitespace and split each side on dots. A side takes the
numeric path only when it has at least one component and every component
satisfies `str.isdigit()`; the numeric path converts each component with
base-10 `int()`, drops trailing zero components, and compares the numeric
tuples. When either side has a component failing `isdigit()`, the trimmed
original strings must be exactly equal. `str.isdigit()` accepts Unicode
digit characters — for example superscript two, `U+00B2` — that base-10
`int()` rejects with `ValueError`, so the current legacy helper in
`state_machine.py` is not total. The pure evaluator must be total and fail
closed: when any component passes `isdigit()` but its `int()` conversion
fails, the whole comparison is a mismatch. It never raises, and it does not
fall back to string equality for such a pair, because the legacy rule never
accepted it (it raised). This does not broaden accepted versions: every pair
the current legacy rule accepts — including Unicode decimal digits that
`int()` does parse, such as fullwidth digits — remains accepted with the
same result, and no previously raising pair becomes accepted.

Gate ordering for this rule is binding. First, the legacy
`strategy_versions_match` helper is hardened with the identical fail-closed
rule (conversion failure is caught and returns mismatch) in a separately
verified fail-closed-only runtime change, accepted no later than Gate
`QP-KER-015` and before any parity gate treats version evidence as safe.
Only then is kernel/legacy parity over the version corpus required, and the
corpus must bind all of these focused regressions:

```text
"²" vs "2"       -> mismatch, no exception
"²" vs "²"       -> mismatch, no exception, no exact-string fallback
"２" vs "2"      -> match
"２.０" vs "2"   -> match after numeric conversion/trailing-zero removal
```

The two superscript cases distinguish the required `isdigit()`-then-guarded-
`int()` rule from an incorrect `isdecimal()` plus string-fallback
implementation; the fullwidth cases preserve versions the legacy helper
already accepts. The kernel may not block a version pair that the hardened
legacy rule accepts, and the parity corpus must also include one equal pair
that matches only under the ordinary non-numeric string fallback.

The exact authority check sequences for `authority_algorithm_version=1` are:

```text
guarded_level_4:
  guarded_autopilot_enabled
  kill_switch_not_engaged
  autopilot_not_paused
  broker_mode_safe
  authority_level_4
  policy_version_match
  policy_identity_match
  broker_health
  quote_not_stale
  strategy_promotion_approved
  strategy_level_allowed
  krx_auto_order_window
  order_type_allowed
  monthly_loss_stop_not_triggered
  monthly_loss_pause_allows_order
  no_unfilled_conflicting_order
  no_unresolved_paper_buy_order
  idempotency_key_new
  fresh_risk_check_passed

automated_level_5 and professional_risk_reduction:
  fully_automated_operator_enabled
  live_trading_disabled
  kill_switch_not_engaged
  operator_not_paused
  broker_mode_safe
  authority_level_5
  policy_version_match
  broker_health
  snapshot_not_stale
  quote_not_stale
  risk_reducing_purpose_verified
  strategy_registry_validated_l5
  strategy_level_allowed
  strategy_recipe_matches_registry
  krx_auto_order_window
  order_type_allowed
  monthly_loss_stop_not_triggered
  monthly_loss_pause_allows_order
  no_unfilled_conflicting_order
  no_unresolved_paper_buy_order
  idempotency_key_new
  fresh_risk_check_passed
```

For a passed authorization, the tuple is the complete sequence and
`authorized == all(check.passed) == true`. For a failed authorization, the
tuple is exactly the prefix through the first false check,
`authorized=false`, and `first_failed_check` equals that entry. Missing,
duplicated, reordered, unknown, or post-failure checks block. Level 1-3 use the
exact short sequences above and cannot reuse Level 4/5 check names to
manufacture assurance.

The kernel does not authenticate a subject or call the authority functions. It
only validates the supplied immutable evidence. Existing human-readable step
details are excluded; `detail_code` is a closed mapping from the check name.

Professional risk-reduction is **blocked in v1** with
`professional_binding_not_supported`. `QP-KER-060B` may enable it only after a
new reviewed `ProfessionalPositionEvidenceV1` carries policy/strategy/symbol,
position quantity and timestamps, reconciled snapshot ID, quote symbol/bid/as-
of, orderable and reserved sell quantity, and the candidate quantity/limit/
notional/target-weight inputs needed to recompute the current reduce-only
predicate. An opaque fingerprint alone is never approval evidence.

#### `SingleRiskEvidenceV1` and `BatchRiskEvidenceV1`

Each is a discriminated `passed | failed | not_evaluated` union. The
`not_evaluated` variant contains no result payload.

```text
SingleRisk evaluated payload:
  risk_check_id
  order_plan_id
  passed
  policy_id
  policy_version
  policy_user_id
  snapshot_user_id
  idempotency_key
  created_at
  expires_at
  passed_checks: tuple
  failed_checks: tuple
  snapshot_id
  snapshot_captured_at
  snapshot_fingerprint_schema_version=1
  snapshot_fingerprint
  submit_market_quote_symbol: optional
  submit_market_quote_as_of: optional
  submit_market_quote_fingerprint_schema_version=1 when quote is present
  submit_market_quote_fingerprint: optional
  guardrail_fingerprint_schema_version=1
  guardrail_fingerprint
  reservation_state: none | required_not_prepared | prepared_authoritatively

BatchRisk evaluated payload:
  passed
  mode: full_batch | partial_batch | rejected
  policy_version
  accepted_order_plan_ids: tuple
  failed_checks: tuple
```

For the first shadow slice, `reservation_state` is `none` for mock/simulated
paper and `required_not_prepared` for external KIS paper. Shadow construction
must occur before `prepare_order()`. `prepared_authoritatively` is reserved for
later read-only observation of an already completed authoritative transaction;
the kernel cannot create it.

The legacy adapter supplies already-computed risk results from the same
submit-time evidence. The kernel does not recalculate portfolio arithmetic and
does not generate a replacement risk-check ID. The current submit-time batch
gate constructs `BatchRiskConfig` without `partial_allow`, so an eligible
submission must carry `mode=full_batch`; proposal-time partial selection is not
authorization to submit a partial batch.

Policy, snapshot, ticket (when present), and operator-run user identities must
agree. All times are aware. `order_expires_at=None` is the semantic block
`order_expiry_missing`. The common temporal relations are:

```text
ticket.requested_at <= ticket.approved_at <= authorization.evaluated_at
direct.approval_transition_at <= authorization.evaluated_at
authorization.evaluated_at <= single_risk.created_at <= input.evaluated_at
single_risk.created_at <= input.evaluated_at < single_risk.expires_at
snapshot_captured_at <= input.evaluated_at
candidate.intent.quote_time <= input.evaluated_at
ticket.approved_at <= input.evaluated_at < ticket.expires_at
input.evaluated_at < candidate.order_expires_at
```

Equality at any expiry deadline is expired. A future authorization, risk,
snapshot, or quote blocks with the stage-specific closed reason. For ordinary
Level 5, the current runtime supplies one captured instant, so
`authorization.evaluated_at == single_risk.created_at == input.evaluated_at`.
For Level 4 and Level 1-3, approval/authorization time may precede but never
follow submit-risk time. KIS requires exact equality between the risk and paper
snapshot ID/time/fingerprint and, when present, the risk and paper market-quote
symbol/time/fingerprint. Same IDs/timestamps with different fingerprints block.

#### `FinalSafetyEvidenceV1`

This is a `passed | failed | not_evaluated` union. Evaluated payload contains
only predicates the current final submission gate actually checks:

```text
captured_at
policy_snapshot_current
policy_kill_switch_engaged
live_trading_enabled
operator_kill_switch_engaged
autopilot_paused
broker_healthy
failed_checks: ordered tuple
```

The only algorithm-v1 final-safety check order is:

```text
policy_version_match
kill_switch_not_engaged
live_trading_disabled
operator_kill_switch_not_engaged
operator_not_paused
broker_health
```

The tuple contains every failed check in that order; `passed` requires an empty
tuple and all corresponding booleans safe. No caller-selected check name is
accepted.

Guarded/full-automation feature flags are authorization predicates and are not
silently added to this final gate. Rechecking them after authorization would be
a separate fail-closed hardening delta with its own parity review.

#### `ExecutionContextSnapshotV1`

All values are captured outside the kernel:

```text
data_mode
run_mode
market_orders_enabled
current_policy_id
current_policy_version
external_paper_enabled
policy_user_id
operator_run_id: optional
```

`data_mode` uses the project-wide closed deployment values from `AGENTS.md`.
`run_mode` is one of:

```text
level_1_2_mock
level_3_direct
level_3_ticket
guarded_level_4
operator_mock_submit
operator_paper_submit
professional_risk_reduction
```

No caller may place an arbitrary route or endpoint string in the evidence.
`operator_dry_run` is not an execution run mode: it terminates at the planning
boundary, calls the execution kernel zero times, and performs zero broker,
coordinator, repository-submission, reservation, or event mutation. A future
planning kernel requires a different contract and verdict vocabulary.

`policy_snapshot_current` is captured by the adapter using the same exact
`UserPolicy` equality that the legacy final gate currently applies and belongs
to `FinalSafetyEvidenceV1`. The context policy ID/version must match candidate
and authorization evidence. `operator_run_id` is required for both mock and
paper Level 5 and must equal the authorization run ID; it is forbidden for
Levels 1-4. For KIS it must also equal the checkpoint and paper run ID.

Secrets, raw account IDs, tokens, request headers, and raw KIS payloads are
forbidden.

#### `PaperSubmissionEvidenceV1`

This is a `passed | failed | not_evaluated | not_applicable` union. Mock and
in-process simulated paper use `not_applicable` once their route reaches the
paper-input stage. An earlier authorization failure uses `not_evaluated`. KIS
paper evaluated variants carry one frozen object built by an authoritative
adapter from the same retained arguments and store/coordinator provenance used
for submission:

```text
paper_run_id
paper_run_fingerprint_schema_version=1
paper_run_fingerprint
run_user_id
run_policy_id
run_policy_version
run_mode
data_mode
checkpoint_status
snapshot_id
snapshot_captured_at
snapshot_fingerprint_schema_version=1
snapshot_fingerprint
snapshot_deadline
quote_symbol
quote_as_of
quote_fingerprint_schema_version=1
quote_fingerprint
quote_deadline
entry_atr14: canonical decimal | explicit none
store_id
account_scope_fingerprint
session_id
fencing_token
session_revision
session_status
session_lease_deadline
```

`checkpoint_status` is the closed Literal string
`started | completed | blocked | failed`; only `started` may create a new
submission. `session_status` is `active | closed | abandoned`; only `active`
with `evaluated_at < session_lease_deadline` qualifies.

Presence booleans are forbidden. Ordinary Level 5 authorization run reference,
context, durable checkpoint, and paper evidence must bind the same run, user,
policy, mode, and data mode. Store provenance, account scope, active session,
lease, fence, snapshot, and quote fingerprints must all match retained legacy
arguments. The facade recomputes the canonical retained-argument fingerprints
both when it constructs the input and immediately before handoff. A public raw
validator result is never accepted directly by a cutover executor.

Fingerprint projections use the section 4.3 canonical encoding and are
purpose-specific:

```text
paper_run_v1:
  paper_run_id, run_user_id, run_policy_id/version, run_mode, data_mode,
  checkpoint_status

snapshot_v1:
  snapshot_id, user_id, cash, equity, daily/monthly loss ratios, captured_at,
  source, positions sorted by the full canonical tuple (normalized symbol,
  sector, quantity, explicit orderable-quantity marker/value, market price),
  with every tuple field retained

quote_v1:
  normalized symbol, last, explicit bid/ask marker/value, as_of

guardrail_v1:
  daily order count/turnover, monthly pause/stop, policy/operator kill,
  broker health and heartbeat/error times, pause, unresolved paper buy,
  sorted unfilled keys, sorted submitted idempotency keys, and reserved sell
  quantities sorted by normalized symbol
```

No raw account ID, token, header, callback, or raw provider/broker payload is in
these projections. Every included-field mutation changes its hash; adapter and
facade must independently produce byte-identical values. `entry_atr14` uses an
explicit none marker or the same canonical Decimal encoding and is compared
directly before handoff. `QP-KER-060B` adds a separately versioned full
professional position projection rather than reusing these hashes.

#### `CapabilityEvidenceV1`

This is a `passed | failed | not_evaluated` union. Evaluated evidence selects
one closed `profile_id`; callers cannot provide free boolean combinations.
Profile semantics are:

| Profile | environment / broker mode | external paper | order types | quantity/price | durable requirements |
|---|---|---:|---|---|---|
| `mock_v1` | `mock` / `mock` | false | limit only; market remains globally disabled | existing simulator validation | none |
| `simulated_paper_v1` | `simulated_paper` / `paper` | false | limit only; market remains globally disabled | existing simulator validation | none |
| `kis_paper_v1` | `kis_paper` / `paper` | true | limit only | whole positive quantity; positive integer KRW limit | prepare, atomic reservation, provenance, active session and fence |

The complete v1 eligible-composition matrix is:

| profile | data mode | run mode | authorization kind | external paper | paper evidence |
|---|---|---|---|---:|---|
| `mock_v1` | `fixture` | `level_1_2_mock` | `simulated_level_1_2` | false | not_applicable |
| `mock_v1` | `fixture` | `level_3_direct` | `human_direct_level_3` | false | not_applicable |
| `mock_v1` | `fixture` | `level_3_ticket` | `human_ticket_level_3` | false | not_applicable |
| `simulated_paper_v1` | `fixture` | `level_3_direct` | `human_direct_level_3` | false | not_applicable |
| `simulated_paper_v1` | `paper_trading` | `level_3_ticket` | `human_ticket_level_3` | false | not_applicable |
| `mock_v1` | `fixture` | `guarded_level_4` | `guarded_level_4` | false | not_applicable |
| `simulated_paper_v1` | `fixture` | `guarded_level_4` | `guarded_level_4` | false | not_applicable |
| `mock_v1` | `fixture` | `operator_mock_submit` | `automated_level_5` | false | not_applicable |
| `simulated_paper_v1` | `fixture` | `operator_paper_submit` | `automated_level_5` | false | not_applicable |
| `kis_paper_v1` | `paper_trading` | `operator_paper_submit` | `automated_level_5` | true | passed |

All other combinations block. Professional rows remain absent until Gate
060B. KIS Level 3 blocks earlier at authorization with
`actor_assurance_missing`. No live environment exists in v1. This does not
claim full KRX tick-size support, which remains a later gate.

### 4.3 Versioned output

`KernelDecisionV1` is frozen and contains no callable command:

```text
schema_version = 1
order_plan_id
verdict: eligible_for_legacy_submit | blocked
blocked_stage:
  identity | candidate | authorization | risk | final_safety |
  paper_evidence | capability | none
reason_codes: sorted unique tuple
durable_prepare_requirement: not_evaluated | not_required | required
atomic_reservation_requirement: not_evaluated | not_required | required
intended_next_stage: none | legacy_submit_handoff
evaluated_at
evidence_fingerprint
```

`eligible_for_legacy_submit` means only that the immutable evidence bundle may
continue through the existing legacy handoff. It never means that an order was
prepared, submitted, accepted, or filled. In particular, it does not predict
the `before_broker_submit` callback or the second final-safety check that the
legacy path performs immediately before durable preparation/broker submission.
Every blocked decision sets `intended_next_stage=none`. Requirement fields are
`not_evaluated` if the capability stage was not reached, otherwise reflect the
closed profile. Eligible KIS paper sets both to `required`; eligible mock and
in-process simulated paper set both to `not_required`.

`evidence_fingerprint` is SHA-256 over a canonical JSON projection of every
input field. Canonicalization uses the Literal string values directly; UTC `Z` timestamps
with exactly six fractional digits; UTF-8 with `ensure_ascii=false`;
`sort_keys=true`; compact separators; and `allow_nan=false`. Decimal values are
finite and formatted from `Decimal.as_tuple()` without consulting the mutable
global Decimal context; equal values (`1.0`, `1.00`, and `1E+0`) use the same
plain non-exponent string and negative zero becomes `0`. Tuple order remains
meaningful. Equal UTC instants with different original offsets fingerprint
identically. No field is omitted to hide nondeterminism. The fingerprint is
diagnostic only and must never become a broker idempotency key or event
identity.

### 4.4 Deterministic decision order

The evaluator follows the observation-phase prefix from section 4.2. Within a
phase it applies the following route order, skipping suffix stages explicitly
marked `not_evaluated`. Each predicate belongs to exactly one stage:

| Stage | Owned predicates |
|---|---|
| `identity` | candidate/current policy ID and version; order/intent identity |
| `authorization` | evaluation prefix/state; kind/run-mode; exact check sequence; policy/user/strategy/ticket/run/actor assurance; professional v1 support |
| `paper_evidence` | for external submission, required snapshot/quote/run first; then exact retained run/snapshot/quote/ATR/store/account/session/fence binding and freshness; external strategy-explanation presence |
| `candidate` | from post-authorization phases: `user_approved`, risk-check presence, and order expiry |
| `risk` | single then batch prefix; risk/order/policy/user/idempotency binding; risk/snapshot/quote time/fingerprint; pass; full-batch membership |
| `final_safety` | exact current legacy checks only: policy snapshot equality, policy/operator kill, live, pause, broker health |
| `capability` | profile/environment/data/run-mode matrix; order type; quantity/price step |

Market-order first failure follows legacy truth: Level 4/5 fail authorization
at `order_type_allowed`; Level 1-3 fail single risk at its same-named check.
`market_order_disabled` at capability is only the defensive result for an
internally inconsistent ready bundle that claims those earlier gates passed.
A provided strategy binding mismatch belongs to authorization; a missing
binding required solely by external KIS prepare is
`paper_strategy_binding_missing` at paper evidence. All applicable failures in
the first failing stage are sorted unique; later stages are ignored.

The v1 semantic decision reason vocabulary is closed:

```text
order_identity_mismatch
policy_identity_mismatch
policy_version_mismatch
order_not_user_approved
order_expiry_missing
order_expired
risk_check_missing
prior_risk_check_expired
evidence_prefix_mismatch
authorization_denied
authorization_evidence_mismatch
authorization_kind_mismatch
execution_mode_mismatch
actor_assurance_missing
ticket_expired
strategy_binding_missing
strategy_binding_mismatch
strategy_authority_mismatch
lifecycle_binding_mismatch
operator_run_mismatch
professional_binding_not_supported
risk_check_mismatch
risk_check_expired
risk_evidence_not_evaluated
risk_quote_mismatch
single_order_risk_failed
batch_risk_failed
batch_order_not_accepted
partial_batch_not_allowed_at_submit
future_evidence_timestamp
policy_snapshot_changed
live_trading_enabled
policy_kill_switch_engaged
operator_kill_switch_engaged
autopilot_paused
broker_unhealthy
paper_evidence_mismatch
paper_strategy_binding_missing
checkpoint_status_invalid
paper_session_status_invalid
data_mode_mismatch
broker_environment_mismatch
market_order_disabled
quantity_step_mismatch
price_step_mismatch
account_provenance_missing
paper_session_fence_missing
broker_capability_mismatch
```

Adapters map existing check names and exceptions to this vocabulary for parity;
arbitrary exception messages and human-readable authority details never become
kernel reason codes.

The separate structural validator error-code vocabulary is exactly:

```text
invalid_evidence_schema
naive_or_invalid_timestamp
```

### 4.5 Structural validation and semantic decisions

The pure API is deliberately split:

```text
validate_kernel_input_v1(raw_snapshot)
  -> valid deeply immutable input
  -> or KernelEvidenceValidationError

evaluate_execution(evidence: KernelEvaluationInputV1)
  -> KernelDecisionV1
```

Structural validation is limited to type/shape, closed Literal values, finite numeric
values, bounded opaque references, and aware timestamps. Kind/run-mode/policy/
strategy/risk binding contradictions remain semantic decisions so their closed
reasons are stable. The public validator catches and sanitizes the underlying
Pydantic error. `KernelEvidenceValidationError` contains only one closed code
and a lexicographically sorted tuple of allowlisted field paths. Unknown keys
are reported as `$extra`, never by their attacker-controlled name. It never
echoes a raw value, Pydantic message/`input_value`, or exception text.

If every structural defect is an invalid/naive timestamp, the code is
`naive_or_invalid_timestamp`; every mixed or other structural failure uses
`invalid_evidence_schema`. The evaluator is total for a constructed input and
does not raise for semantic allow/block conditions.

The validator aggregates three immutable finding tuples before choosing that
code: raw-preflight findings, sanitized Pydantic `errors(...)`, and the closed
timestamp-path scan of the safe detached tree. Pydantic validation therefore
runs even when preflight found a safely replaceable defect; no invalid raw
object is passed to it. Only a zero-finding successful Pydantic model is
returned. This ordering is what makes the two-naive and mixed-defect fixtures
independent of traversal/Pydantic fail order.

The initial shadow runner maps a construction error to a structured
non-authoritative `evidence_error` comparison. It neither changes nor suppresses
the legacy verdict. A later cutover wrapper must convert the same error to a
blocked result before the first submission-phase mutation.

The input carries no caller-supplied evidence fingerprint. The evaluator
computes the output fingerprint once. A later executor-envelope contract may
recompute and compare that fingerprint, but v1 does not pretend to verify a
nonexistent input fingerprint.

### 4.6 Submission evidence retained outside the pure decision

The current submission call also carries full `PortfolioSnapshot`, optional
`ManagedPositionBinding`, optional `Quote`, `paper_run_id`, optional
`entry_atr14`, and optional `before_broker_submit` callback. V1 does not embed
these mutable/domain objects or a callable in its fingerprinted pure input.

This is not permission to drop them. The shadow/cutover adapter must retain the
original values and pass them unchanged to the existing legacy submission
boundary after an eligible decision. `PaperSubmissionEvidenceV1` binds their
canonical fingerprints and provenance; presence booleans are not evidence.
The external paper coordinator remains responsible for its stronger full
request fingerprint over order, snapshot, quote, ATR, buying power, and reserve
basis.

The pure evaluator never calls the submission fence. During later cutover the
side-effecting facade must preserve the existing order exactly:

```text
first final-safety snapshot
-> eligible KernelDecision
-> first temporal fence over order/risk/quote/snapshot
-> OrderStatus.submitted transition only after the fence
-> before_broker_submit fence callback
-> new clock read and second final-safety + temporal fence
-> durable prepare/reservation when external paper
-> pre-claim durable deadline/fingerprint/session/fence check
-> claim
-> new clock read and final pre-POST deadline/session/fence check
-> exactly one broker submit
```

Tests compare object snapshots/identities at the adapter boundary and prove the
callback is invoked exactly once only by the authoritative executor, never by
shadow evaluation.

Gate 015 uses the existing canonical `order_plan_payload`; it does not add a
schema column. On new prepare, the stored submission-evidence deadline is:

```text
min(order_expires_at, risk_expires_at, quote_deadline, snapshot_deadline)
```

For existing schema-v11 rows, reopen/cleanup strictly parses the aware
`expires_at` from `order_plan_payload` and uses
`min(existing submission_evidence_expires_at, payload order expiry)`. Missing,
naive, or corrupted expiry is never guessed: a still-prepared row is
terminalized locally with one atomic reservation release/event and POST=0; a
claimed/unknown row becomes reconciliation-required with reservation held.
Re-entry also compares current order expiry to the payload value. This choice
requires no migration/backfill but requires a real v11 reopen fixture.

Session lease is a separate authority fence, not market-evidence freshness. A
prepared row may retain the existing atomic takeover behavior only after it is
rebound to a new active session/fence; all order/risk/quote/snapshot deadlines
must still be fresh. Every preclaim and final pre-POST boundary rechecks active
session, lease, revision, and fence. Expiry at or before preclaim, or after
claim but before the client call while no POST has begun, becomes a definitive
local rejection with reservation release and event append in the same
authoritative transaction. A dispatch already claimed with an ambiguous
outcome is never re-POSTed or auto-released merely because time later expired;
it remains reconciliation-only with reservation held.

Before prepare, `SingleRiskEvidenceV1.submit_market_quote_fingerprint` must
equal the paper quote fingerprint whenever submit risk consumed a market quote;
KIS requires it. The facade's construction-time and pre-handoff recomputation
both cover run, full snapshot, full quote, `entry_atr14`, and, after Gate 060B,
the full managed-position projection. Any mutation yields
`paper_evidence_mismatch`, prepare=0, and POST=0.

The facade never receives or calls `KisPaperClient` and never calls
`submit_prepared_order()` directly. For external paper it may ask the existing
coordinator to prepare at most once and the existing broker adapter to submit
at most once. The sole normal KIS POST remains
`KisPaperClient.place_limit_cash_order()` inside the coordinator. Retry loops
and exception-driven resubmission are forbidden. Reservation lifecycle and
canonical event append remain owned by `PaperStateStore` transactions; neither
kernel nor facade creates, releases, repairs, or replays them directly. A
known dispatch re-entry routes only to replay/reconciliation and cannot POST.

## 5. Mode and composition contract

The later shadow runner reads `EXECUTION_KERNEL_V2_MODE` at the composition
boundary and converts it to a closed mode value:

```text
off       # default; kernel is not constructed or called
shadow    # pure evaluate + non-authoritative comparison only
```

There is no `cutover`, `enforce`, or `live` value in the first feature. An
unknown value fails application composition before any order work. Parsing the
configuration is outside `kernel.py`; the pure evaluator never reads the
environment. The missing-variable default is exactly `off`; no `.env.example`
edit is required for the first slice.

In `shadow` mode:

- legacy authorization, risk, transition, reservation, event, and broker paths
  remain authoritative;
- the kernel receives deep-copied evidence;
- the returned decision cannot call anything;
- no result is written to the repository, audit log, schema-v11 store, or
  broker;
- no KIS client method, including buying-power queries, may be called to build
  shadow input;
- no `prepare_order()`, reservation, claim, or dispatch mutation is permitted;
  and
- external-paper shadow tests use already captured fake evidence only.

The first pure-model task need not add mode parsing or integrate a runner. Mode
and runner are a separate audited task after the contract is accepted.

## 6. Shadow integration point and parity

### 6.1 Integration point

There is no single success-only hook that can satisfy blocked parity. The
shadow task therefore has stage-local, read-only observation adapters:

```text
authorization-failure hook
  -> before Level 4/5 or Level 3 approval blocked audit/return; plan may still
     be pre-user-approved
external-paper-input-failure hook
  -> at the first explicit snapshot, quote, or paper-run check at the start of
     submit_order_plan, before candidate/risk evaluation
candidate/status failure hook
  -> before risk-check-required/expired/approval-required mutation or return
single-risk failure hook
  -> before risk failure audit/status mutation
batch-risk failure hook
  -> before batch failure audit/status mutation
final-safety failure hook
  -> before the first, pre-transition final-safety audit/status mutation
capability failure hook
  -> before a pure pre-transition capability rejection is mutated
ready-to-submit hook
  -> after all pass evidence exists and before the first submission mutation
```

The post-transition callback, second final-safety fence, and coordinator
prepare rejection are not execution-kernel shadow phases because the order is
already `submitted`. They remain covered by Gate 015 and the side-effecting
facade's exact counter/failure contract. Kernel output deliberately does not
predict them. A future post-transition observer requires a different
non-submission verdict and review.

Each adapter constructs the immutable prefix actually evaluated and marks only
later stages `not_evaluated`. It may not run a skipped gate merely to complete
the bundle. The ready-to-submit adapter runs before:

```text
OrderStatus.submitted transition
DurablePaperSubmissionCoordinator.prepare_order
broker.submit_order
any repository, audit, reservation, dispatch, or event write caused by the
  submission phase after the evidence snapshot
```

Planning and professional checkpoint writes remain outside the execution
kernel. Approval/risk observation hooks must precede only the specific legacy
mutation or blocked return they compare; the evaluator itself performs zero
writes. After a ready decision, the legacy path retains its current submission
ownership, subject to the separately reviewed expiry fences.

Adding the runner must not add a second broker adapter or submission callable.
The runner constructor has no broker/store/repository/audit parameter.

Level-specific services retain their current error translation and fallback
behavior. The runner only consumes an adapter snapshot.

### 6.2 Normalized parity projection

Parity compares:

```text
order_plan_id
eligible-for-legacy-submit/block
first blocked stage
normalized reason-code set
durable-prepare requirement state
atomic-reservation requirement state
```

It does not compare:

- newly generated IDs;
- audit/event counts;
- wall-clock values not explicitly supplied;
- broker order IDs or fill IDs;
- mutable object identity;
- human-readable detail text; or
- eventual broker outcome.

Legacy parity evidence must be derived from the same captured inputs. It may
not be inferred from a later fill or terminal order state.

Blocked-outcome normalization is closed. Verdict and first blocked stage must
be exactly equal, and the legacy-translated reason codes must be a non-empty
subset of the kernel's sorted unique reason set for that stage, including the
mapped legacy first failure. The kernel may carry additional same-stage
reasons only when they are derivable from the same captured evidence bundle;
any other difference is a mismatch. This rule exists because the legacy path
fails fast inside the candidate and external-paper-input stages (one raised
exception) while the kernel reports every same-stage defect. Exact reason-set
equality is therefore asserted with single-defect fixtures; deliberately
multi-defect fixtures assert the subset rule. Neither form relaxes stage or
verdict equality, and a kernel reason from a different stage is always a
mismatch.

KIS paper Level 3 is excluded from every parity corpus, including the Gate 065
rehearsal: the kernel blocks it at authorization with
`actor_assurance_missing`, while the current legacy KIS composition blocks the
same call later at the explicit paper-input stage because Level 3 endpoints
pass no snapshot/quote/run arguments. This stage difference is deliberate
stricter-kernel precedence, not parity evidence, until the separate
authenticated-subject binding mission defines the Level 3 external route.

The known current-order expiry delta is handled explicitly: a kernel
`order_expired` block versus a legacy allow is a real mismatch. A separately
reviewed expiry hardening task must implement every temporal/durable fence in
section 4.6 before ready-path shadow parity can be accepted. One pre-submitted
check alone is not sufficient. Tests may not suppress the mismatch.

### 6.3 Mismatch policy

- Unit/integration parity tests fail on any mismatch.
- The initial production `off` default makes mismatches impossible in ordinary
  operation.
- Enabling `shadow` is allowed only in fake/mock development profiles covered
  by the parity tests.
- A shadow mismatch cannot authorize, submit, retry, repair, release capacity,
  or change the legacy verdict.
- No mismatch is silently counted as parity. The runner returns a structured
  comparison to its caller; a later observability task may choose a
  non-authoritative sink under a separate contract.
- KIS paper shadow remains disabled until `QP-KER-065` fake-KIS rehearsal and
  its independent audit are accepted.

## 7. Required first-slice tests

`quantpilot/tests/unit/test_execution_kernel_v2.py` must prove:

1. two evaluations of identical input are exactly equal;
2. input objects are unchanged after evaluation;
3. output contains no callable and cannot perform a command;
4. the module passes the import allowlist, the dynamic import/I/O/clock/ID
   AST bans, the closed module-scope statement/expression policy (a fixture
   module containing `CHECKS = sorted(("b", "a"))` at module scope is
   rejected), and the recursive runtime module-global immutability scan (the
   same list value injected as a module global is rejected), and the
   recursive model tree contains no mutable/open-ended type;
5. naive time, mismatched policy, stale risk, failed authorization, failed
   single risk, failed batch, absent batch membership, kill, live, pause,
   unhealthy broker, unsupported order type, and missing paper provenance all
   fail closed at the correct stage;
6. a valid mock Level 3 bundle returns `eligible_for_legacy_submit`;
7. valid Level 4/5 evidence can be represented without the kernel calling the
   authorization functions, while every omitted/reordered/duplicated/mutated
   authority check blocks;
8. external KIS evidence returns only declarative durable requirements;
9. fake broker/client/store/repository/audit sentinels have zero calls; and
10. canonical fingerprinting is stable across Decimal spellings, negative
    zero, UTC offsets, and source-container mutation, and rejects
    secret-shaped fields without leaking attacker keys or values.

The shadow-runner task additionally proves:

- mode defaults to `off` and rejects unknown values;
- off mode constructs no evaluator;
- shadow execution occurs before the first submission-phase authoritative
  mutation after its evidence snapshot;
- repository, audit, event-store, coordinator, broker, and fake-client
  snapshots/counters are identical before and after shadow-only evaluation;
- Level 1-2 mock, Level 3 direct/ticket in mock or their explicitly listed
  simulated-paper rows, Level 4 blocked/success, and Level 5 blocked/success
  evidence produce expected normalized parity at every real first-failure hook;
- operator dry-run invokes kernel, broker, coordinator, and repository
  submission mutation zero times;
- professional risk-reduction stays closed in v1 until `QP-KER-060B` supplies
  full recomputable position evidence;
- `outcome_unknown`, restart, kill, and reconciliation tests retain their
  existing no-rePOST counters; and
- the static sole-POST-authority regression remains green.

The Level 4 success fixture is not inferred from the existing two blocked-path
tests. At governing main `70219d4` it is reproduced by injecting an approved
default strategy whose allowed levels include `level_4`/`guarded_autopilot`, injecting
an open KRX auto-order window, and using a policy with authority level 4,
guarded execution, guarded policy flag true, and mock broker. The current
fixture then submits three orders and records three fills with live disabled.
The parity test must build these dependencies explicitly; it must not weaken
the production default strategy or global safe flags.

The minimum pure-model case matrix is binding:

| Case | Expected result |
|---|---|
| valid Level 3 direct fixture/mock with `unverified_local` | `eligible_for_legacy_submit` |
| valid Level 3 direct fixture/in-process simulated paper with `unverified_local` | eligible |
| valid local ticket Level 3 in fixture/mock or simulated paper | eligible |
| any Level 3 direct/ticket evidence with KIS profile at current baseline | `blocked/authorization/actor_assurance_missing` |
| valid policy-authorized Level 4 | eligible |
| valid operator-authorized Level 5 | eligible |
| professional reduce-only evidence in v1 | `blocked/professional_binding_not_supported` |
| candidate policy/order identity disagreement | `blocked/identity` stage |
| candidate not `user_approved` | `blocked/candidate/order_not_user_approved` |
| candidate has no prior risk-check ID | `blocked/candidate/risk_check_missing` |
| candidate prior risk-check evidence is expired before fresh risk runs | `blocked/candidate/prior_risk_check_expired` |
| current order expiry reached | `blocked/candidate/order_expired` |
| denied or internally inconsistent authorization | `blocked/authorization` stage |
| Level 4/5 authorization and candidate strategy bindings disagree | `blocked/strategy_binding_mismatch` |
| recipe/registry version pair equal only under the legacy non-numeric string fallback | eligible; kernel matches `strategy_versions_match` |
| `"²"` vs `"2"` and `"²"` vs `"²"` | `blocked/authorization` with the closed version-binding reason; no exception; no string fallback |
| `"２"` vs `"2"` and `"２.０"` vs `"2"` | eligible when all other evidence passes; numeric Unicode behavior preserved |
| risk ID/policy/idempotency disagreement | `blocked/risk_check_mismatch` |
| expired fresh-risk evidence | `blocked/risk_check_expired` |
| failed single or batch risk | the corresponding closed risk reason |
| current order absent from accepted batch IDs | `blocked/batch_order_not_accepted` |
| submit-time batch reports `partial_batch` | `blocked/partial_batch_not_allowed_at_submit` |
| current policy snapshot changed before submission | `blocked/policy_snapshot_changed` |
| live, either kill, pause, or unhealthy broker | matching `final_safety` reason |
| Level 4/5 market order | `blocked/authorization/authorization_denied` with first failed check `order_type_allowed` |
| Level 1-3 market order | `blocked/risk/single_order_risk_failed` with failed check `order_type_allowed` |
| internally inconsistent ready bundle claiming earlier market checks passed | `blocked/capability/market_order_disabled` |
| KIS paper fractional quantity or non-integer KRW limit | matching capability-step reason |
| external paper with any run/snapshot/quote/store/account/session/fence mismatch | `blocked/paper_evidence_mismatch` or the narrower provenance/fence reason |
| external paper without the strategy explanation required by prepare | `blocked/paper_evidence/paper_strategy_binding_missing` |
| valid external-paper evidence | eligible with both durable requirement flags, zero calls |
| same valid input evaluated twice | byte-equivalent decision/fingerprint |
| mapping order differs but semantic input is equal | identical fingerprint |
| equal Decimal values with different spellings / equal UTC instants with different offsets | identical fingerprint |
| source list/dict mutated after validation | input, decision, fingerprint unchanged |
| extra `api_key`/`account_number`/raw response field | sanitized structural error; secret absent from text |
| naive timestamp or non-finite numeric | sanitized structural error |
| two naive timestamps and no other defect | `naive_or_invalid_timestamp`; both allowlisted paths sorted |
| one naive timestamp plus any other structural defect | `invalid_evidence_schema`; no fail-fast timestamp misclassification |
| multiple failures in one stage | sorted unique reasons from that stage only |
| failures in different stages | reasons from first stage only; suffix `not_evaluated` |
| authorization failure on a pre-approved-state proposal | authorization reason; candidate and suffix not evaluated |
| single-risk failure | batch payload absent and all later stages not evaluated |
| external KIS missing explicit quote/run/snapshot | paper-evidence reason before candidate/risk |

The expiry/durable hardening corpus is also binding before any cutover:

- a callback crossing each of order, risk, quote, or snapshot deadline yields
  prepare=0 and POST=0;
- `now == expires_at` blocks;
- expiry after claim hook but before the KIS client call yields POST=0 and one
  atomic definitive rejection/release/event when no POST began;
- prepared/restart expiry terminalizes without POST and releases once;
- claimed or `outcome_unknown` restart/expiry never POSTs or auto-releases and
  remains reconciliation-only;
- changing current order expiry on re-entry fails the durable identity check;
- callback/second-fence/prepare failures yield POST=0;
- accepted, rejected, and `outcome_unknown` first attempts call POST exactly
  once; replay/restart never increments that count; and
- insert/transaction faults roll back reservation/event/dispatch with POST=0.

## 8. Gate sequence

| Gate | Outcome | Authority change |
|---|---|---|
| `QP-KER-000` | accepted contract, workboard, and counterpart review | none |
| `QP-KER-010` | pure frozen model/evaluator and unit tests | none |
| `QP-KER-015` | legacy and durable order-expiry/TOCTOU hardening with v11 reopen coverage, plus the total fail-closed `strategy_versions_match` legacy helper, with independent P0/P1=0 | none; fail-closed only |
| `QP-KER-020` | default-off mock shadow runner and normalized comparison | none |
| `QP-KER-030` | exhaustive Level 1-5 mock/simulated-paper parity corpus | none |
| `QP-KER-035` | common side-effecting facade preserving legacy arguments and sole submit call | none |
| `QP-KER-040` | Level 3 direct/ticket handoff cutover | only after separate audit; no new broker |
| `QP-KER-050` | Level 4 handoff cutover | existing guarded authority only |
| `QP-KER-060A` | Level 5 ordinary handoff cutover | existing Level 5 authority only |
| `QP-KER-060B` | professional reduce-only full evidence/recomputation and handoff cutover | existing risk-reduction authority only |
| `QP-KER-065` | fake-KIS shadow rehearsal through the actual SQLite/coordinator composition | none |
| `QP-KER-070` | KIS paper cutover through the unchanged durable coordinator | existing paper authority only |
| `QP-KER-080` | remove proven duplicate legacy orchestration | no authority change |

`QP-KER-020` depends on accepted Gate 015 and its independent P0/P1=0 audit.
`QP-KER-065` uses the actual `kis_paper` composition with a fake KIS client and
real SQLite store/coordinator. Shadow construction performs zero store/client/
coordinator/audit/event mutation and no buying-power query. It covers expiry,
restart, claim, `outcome_unknown`, kill, reconciliation, and external-paper
parity; independent P0/P1=0 is required. `QP-KER-070` remains separately
default-off behind a reversible flag. Every cutover is separately reversible
and starts disabled. KIS is last.
`partial_allow`, outbox/single-writer, ledger, and continuous runtime remain
subsequent roadmap missions.

The exact Gate-070 flag name and its closed composition matrix with
`EXECUTION_KERNEL_V2_MODE`, profile, data mode, and unknown-value handling are
an acknowledged nonblocking P2 for Gates 010/015. Gate 070 may not move to
`ready` until that P2 is closed in a reviewed contract/ADR; unknown or
unsupported combinations must fail closed and the flag must default false.

## 9. Gate acceptance and rollback

Minimum checks for every implementation commit:

```powershell
python -m pytest quantpilot/tests/unit/test_execution_kernel_v2.py -p no:cacheprovider --basetemp=.pytest_tmp_kernel
python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp_kernel_full
python -m quantpilot.jobs.run_smoke
git diff --check
```

Relevant existing focused suites must also remain green:

```powershell
python -m pytest `
  quantpilot/tests/integration/test_level3_flow.py `
  quantpilot/tests/integration/test_level4_guarded_flow.py `
  quantpilot/tests/integration/test_level5_operator_run_once.py `
  quantpilot/tests/unit/test_approval_tickets.py `
  quantpilot/tests/unit/test_harness_batch_risk.py `
  quantpilot/tests/unit/test_external_paper_harness_integration.py `
  quantpilot/tests/unit/test_paper_submission_coordinator.py `
  quantpilot/tests/unit/test_paper_execution_shadow_parity.py `
  -p no:cacheprovider --basetemp=.pytest_tmp_kernel_focused
```

No frontend verification is required unless a later task explicitly changes
the frontend contract.

Rollback for the pure and shadow slices is deletion of the new module/tests and
leaving mode `off`. No database migration, event rewrite, reservation change,
or broker reconciliation is needed. A cutover gate cannot claim rollback by
changing historical authoritative rows; it must switch the relevant adapter
back to the accepted legacy handoff and re-run the full parity suite.

## 10. Completion conditions for Kernel v2

The whole `QP-KERNEL-V2` gate is complete only when:

- Level 3 direct/ticket, Level 4, Level 5 ordinary/professional, and KIS paper
  all pass through one typed kernel handoff;
- level differences are confined to authorization-evidence adapters and
  caller-specific reporting/fallback translation;
- no alternate broker POST path exists;
- KIS paper still uses the same coordinator reservation/claim/no-rePOST
  protocol;
- all canonical events remain atomically paired with authoritative paper
  mutations;
- parity, race, crash, restart, kill, and reconciliation tests pass;
- default safe flags and mock broker remain unchanged;
- independent review reports P0=0 and P1=0; and
- real KIS validation is still correctly reported as manual Gate P evidence,
  not implied by fake-only tests.

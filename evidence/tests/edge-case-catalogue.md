# Edge-case catalogue

Every behaviour the edge-case suites defend, by subsystem. A test name states the
claim it defends, so this list is the specification of what the system must handle.
`xN` means the claim is checked against N inputs.

## `tests/contract/test_edge_domain_capability_schema.py` -- 190 tests

- plain semver versions are accepted (x4)
- malformed versions are rejected with a semver message (x11)
- version must be ascii semver (x5)
- schema version constant is the default stamp
- an unknown field is rejected at every depth (x29)
- a renamed python attribute is not a second spelling of a wire key
- recovery attempts are bounded on both sides (x7)
- recovery attempts cannot be declared unbounded
- fractional attempts are not rounded into range
- a predicate wait without a predicate is refused
- a predicate wait with a predicate is accepted
- there is no sleep mode
- wait defaults to a condition not a duration
- an undeclared input is found wherever it hides (x8)
- a declared input referenced deep inside is accepted
- secret references need no declaration
- a value carrying two reference kinds is ambiguous and refused
- duplicate outcome codes are refused
- duplicate recovery rule ids are refused
- duplicate input or output names do not reach the tool contract (x2)
- sensitive names is exactly the declared set (x4)
- a name sensitive on both sides is reported once
- max risk is the riskiest step wherever it sits (x5)
- hash does not depend on mapping key order (x3)
- hash does not depend on yaml layout
- every mutation changes the hash and no two collide
- canonical form uses wire names and excludes the seal
- hash is sha256 lowercase hex
- sealing is idempotent and ignores a stale declared hash
- an unsealed capability is not valid against its own hash
- a declared hash must match byte for byte
- hash verification can be waived but a wrong hash is otherwise fatal
- the mismatch message names the artifact and both hashes
- visually identical unicode forms are different artifacts
- in place mutation of a nested mapping is blocked or detected
- awkward strings survive a seal dump load cycle (x42)
- a next line character survives a seal dump load cycle
- dump is a fixed point after one generation
- a large flow round trips with a verifying seal
- a very long description round trips exactly
- crlf and bom do not change what a document says
- a declared empty collection round trips (x3)
- an uncompilable regex is rejected when the artifact loads (x8)
- python object tags are refused not executed
- a document that is not a mapping is a validation error (x5)
- unparseable yaml never yields a partial capability
- a bare on key survives loading
- every value type maps to one json schema type (x7)
- json schema carries only what was declared
- a unicode pattern is exported verbatim
- tool name replaces every dot and nothing else
- tool description falls back to the title
- optional inputs are declared but not required
- a capability with no inputs still exports a closed object schema
- outputs are not all promised because a business outcome returns none
- outcome descriptions fall back to their code
- tool schema is json serialisable and survives a round trip
- exported schema is deterministic
- exported schema is closed everywhere
- exported schema top level shape is stable
- the checkpoint is required by the schema export as well as the validator
- the wire spelling of the predicate discriminator is assert

## `tests/integration/test_edge_catalog_store.py` -- 117 tests

- a bare id resolves to the numerically newest version (x5)
- the listing is ordered by id then version whatever the file names say
- an exact pin ignores newer versions and the bare id moves when one arrives
- a version that is not plain semver is unreadable never ordered (x14)
- lookup is exact with no prefix range wildcard or unicode forgiveness (x22)
- injection shaped lookups are inert and the miss stays on one line (x9)
- a path is never a lookup key
- a miss and a refusal are different kinds of error
- an empty or missing or non directory root is an empty catalog
- only top level yaml files are catalog entries
- two files claiming one ref are both listed and resolution is stable
- a hostile file is reported unreadable and does not hide its neighbours (x9)
- a yaml object tag is refused and never executed
- a broken file is named in a miss without echoing what was in it (x2)
- the unreadable list describes the latest listing only
- every in place edit is tampering and is refused but still listed (x17)
- an edit that changes no content does not break the seal (x6)
- removing the seal makes a draft that is served and flagged
- stripping the seal from an approved artifact does not keep the approval
- an unsealed draft cannot be approved even by a record pinned to its computed hash
- unicode text survives sealing and reloading byte for byte (x19)
- concurrent readers all get the same verified capability
- a large catalog lists completely and deterministically

## `tests/integration/test_edge_cli_commands.py` -- 62 tests

- version prints the package version and exits zero
- help lists every command a reader is told about
- every command documents itself (x8)
- an unknown command is a usage error not a crash
- catalog list shows the reference capability and its integrity
- catalog show prints the artifact and resolves a bare id
- catalog show tool schema is json an agent can load
- catalog show of something that is not there is a clean refusal (x7)
- codegen writes a test that is valid python
- codegen for an unknown capability is refused without writing anything
- input pairs split on the first equals and keep the value verbatim (x8)
- a malformed input pair is a usage error (x5)
- replay of a file that does not exist is a usage error
- replay of a directory is a usage error
- sign in without a base url says what is missing
- a malformed input pair stops replay before anything is launched
- replay of a document that is not a capability is refused cleanly (x5)
- replay of an artifact edited after it was sealed is refused
- catalog invoke validates its arguments before acting
- discover without a model key says so instead of starting
- a target problem becomes a sentence and the right exit code (x3)
- the wrapper does not swallow a commands own exit code
- the wrapper does not hide a genuine defect
- the wrapper keeps the commands options working
- replay against a target that is not running is a clean error
- a value outside the declared pattern fails before the browser moves (x7)

## `tests/integration/test_edge_hitl_broker.py` -- 50 tests

- a second escalation is refused and opens nothing (x3)
- every escalation gets its own id and points at its evidence
- degenerate escalation text does not crash the path (x8)
- claiming an unknown intervention changes nothing
- the second claimant loses and leaves the first undisturbed
- an intervention that was already resolved cannot be claimed again
- a claim that fails leaves the session claimable
- a hold of zero is reclaimed by the first sweep
- a human action needs a live human hold (x6)
- a lapsed hold says so when the operator acts
- raw input without a handoff is refused even if the lease says human
- each operator action is counted once in the handoff
- a new claim starts a fresh handoff count
- releasing with no handoff is refused
- a handoff without both snapshots is reported as not captured (x3)
- an unchanged surface is reported as unchanged
- the evidence names an edited field but never carries what was typed
- a refused second release does not rewrite what the operator did
- resume needs a release first
- resume is not repeatable
- a whole cycle leaves an ordered actor tagged trail
- sweeping a session nobody let lapse does nothing (x4)
- a sweep reclaims once and repeated polls do not advance the epoch
- an operator who returns after the sweep cannot hand back or act
- a session whose operator walked away can be taken by someone else
- lease events redact what an operator name or reason carries
- the operators queue is redacted like every other sink (x2)
- three full cycles in a row never reuse or reopen anything
- the redaction probe itself runs
- redacting one huge token finishes within a bounded time

## `tests/integration/test_edge_hitl_queue.py` -- 36 tests

- pending is oldest first whatever order requests arrived in
- requests with identical timestamps keep a stable order
- only open or abandoned requests are pending (x3)
- an abandoned request returns to the queue flagged and can be claimed
- an empty queue has nothing pending
- only an open or abandoned request can be claimed (x3)
- only a claimed request can be released (x4)
- acting on an unknown request raises key error (x4)
- get returns none for an unknown id rather than raising
- the full lifecycle records who did what and when
- abandoning records why so the next operator can tell it from untouched work
- the card has exactly the keys the console reads
- the card carries no operator state
- optional card fields are none not missing or the string none
- the card hands text over verbatim (x11)
- reopening the same id is visible in the queue not silently duplicated

## `tests/integration/test_edge_mock_app_auth.py` -- 124 tests

- every route behind the sign on redirects an unauthenticated caller to login (x18)
- an unauthenticated caller cannot tell a real member from an absent one
- the pages a signed out browser needs are reachable without a session (x6)
- the signed out login page shows no identity and no sign off link
- a route refuses every verb it does not declare (x45)
- method not allowed is decided before the sign on
- an unknown route is a 404 not a login redirect and never a page
- sign on accepts only the advertised credentials exactly (x24)
- a failed sign on echoes neither the operator id nor the password
- a failed sign on does not reveal whether the operator exists
- sign on with no form body is a failed sign on not a server error
- sign on ignores a redirect target in the query string
- a successful sign on sets one http only cookie and no secret
- signing on twice is harmless
- every credential the sign on screen advertises actually signs on (x2)
- an empty or blank session cookie is not a session (x3)
- the session cookie value is escaped where the banner prints it
- the banner shows the signed in operator and a sign off link
- a session belongs to one client not to the server
- sign off clears the cookie and returns to the frameset
- signing off twice and signing off when signed out are harmless
- sign off can be followed by a fresh sign on
- query strings are never reflected into a page
- html pages declare a charset so non ascii survives the trip
- the launcher binds loopback only unless told otherwise
- the launcher serves the requested variant
- the launcher refuses bad arguments before starting a server (x6)

## `tests/integration/test_edge_mock_app_faults.py` -- 132 tests

- a fault manifests exactly as many times as it was armed (x12)
- an indefinite fault never clears and never changes its count (x4)
- an unarmed fault never manifests (x4)
- count zero disarms a fault (x4)
- omitting the count arms exactly one trigger (x4)
- rearming overwrites the remaining count instead of adding to it (x4)
- a consumed fault can be armed again (x4)
- any negative count is unbounded not silently disarmed (x3)
- a huge count is consumed one at a time without overflow or unbounded behaviour (x3)
- different fault kinds are independent of each other
- reset clears every armed fault and is idempotent
- each app instance owns its own fault state
- control state reports the variant it is serving (x2)
- a transient fault is surfaced by every guarded route and consumed once (x7)
- unguarded routes neither fail nor consume an armed fault (x8)
- logout does not consume an armed fault
- the frameset loads cleanly while the content frame takes the fault
- a 502 outranks the login redirect for an unauthenticated request
- armed faults are consumed in the documented order one per request
- an unauthenticated request does not consume the undeclared dialog
- the 502 is a real 502 with no member data and leaves the session alone
- a 502 on a post does not perform the action
- a session timeout clears the cookie and the next request needs a fresh login
- re authenticating after a timeout resumes the flow
- repeated timeouts each require their own re authentication
- a session timeout on a post discards the submission
- the dialog blocks the page but keeps the session and offers continue
- continuing from the dialog reaches the page once the fault is spent
- an indefinite dialog traps continue in a loop of dialogs
- a dialog swallows a post so the action never happens
- the dialog is served in the tenants own stylesheet
- the continue handler is a single inert string literal for ordinary paths (x2)
- a hostile path cannot break out of the dialogs continue handler (x2)
- validation error fires on the next submission whatever it contains
- validation error is not consumed by anything but a real submission (x4)
- an unauthenticated submission does not consume validation error
- a dialog in front of the form keeps the validation error armed
- a rejected submission rerenders the same form so it can be retried
- an unknown fault name is rejected and arms nothing (x14)
- a malformed arm payload is rejected without a partial arm (x9)
- a non object arm body is a client error (x6)
- a form encoded arm is refused rather than half understood
- a rejected arm does not leak source or a traceback to the caller (x2)
- the control endpoints work without a session
- arming an unknown fault raises and names every valid fault
- checking an unarmed fault does not create state
- the snapshot is a copy not a live view
- arming with the enum member and with its string is the same fault
- every fault kind is armable by its public name (x4)
- the documented fault vocabulary has not drifted

## `tests/integration/test_edge_mock_app_live.py` -- 8 tests

- a timeout mid journey lands a real browser on the login screen and recovery resumes
- a search follows its redirect to the record over the wire
- a very long member id in the url is a plain no such member (x3)
- a rejected arm returns a bare error over the wire with no traceback
- two clients share one fault but not one session
- the static stylesheet is served with the right type and is stable

## `tests/integration/test_edge_mock_app_pages.py` -- 289 tests

- the seeded members are exactly the five the docs promise
- every seeded account status is one the data model declares
- find member canonicalises whitespace and nothing else (x10)
- savings account is none for a member without one and not a checking fallback
- a seeded member renders the same record in both tenants (x8)
- the savings summary row is present only when a savings account exists (x8)
- searching a visible member redirects to its canonical record (x8)
- a restricted member is denied without leaking any of the record (x2)
- the three answers are distinguishable and never mistaken for each other (x2)
- looking up an absent member is identical by url and by form (x2)
- repeated lookups are byte identical for every outcome
- an unrecognisable id posted to search is a plain no such member (x27)
- an unrecognisable id in the url is a plain no such member (x25)
- whitespace padding is ignored consistently by form and url (x4)
- a blank member number is a form error not a lookup (x5)
- a search without the field at all is the same form error
- each tenant ignores the other tenants member field name
- a duplicated member field resolves deterministically to one outcome (x2)
- a hostile id is echoed as text and never becomes markup (x7)
- a hostile id in the url is echoed as text too
- path traversal shapes never reach a record or a file (x10)
- a missing field is a field level error on the same form (x16)
- posting the other tenants field names is a missing field error (x2)
- the deposit amount alone never causes a rejection (x13)
- the confirmation reference is derived from the inputs alone (x10)
- a hostile account type is echoed as text not markup
- a hostile deposit is echoed as text not markup
- the confirmation is built from the canonical member not the padded url
- submitting twice is idempotent and the fixture holds no state
- the subaccount routes report an absent member as no such member (x2)
- a restricted member is denied on the subaccount routes too (x2)
- variant b is the same page skeleton as base with only its skin changed (x16)
- the two tenants do not serve byte identical pages (x16)
- no page carries the other tenants classes or field names (x32)
- the tenants share no css class and no form field name
- case a fields keep their label and case b fields do not
- unknown variant keys are refused with the valid choices named
- each tenants stylesheet is served and styles every class the markup emits (x2)
- every page links its own tenants stylesheet only (x2)
- no member flow page carries an id or a data attribute (x28)
- subaccount controls stay unnamed on the form and on every error rerender (x2)
- each unnamed control sits in the row whose label cell names it (x2)
- the search field is the one control that does carry a name (x2)
- the frameset has the same two named frames in every tenant (x2)
- the content frame opens on login unless a session cookie exists (x3)
- the content frame opens on search after sign in and on login after sign off
- the nav frame is reachable without a session and has no real hrefs (x2)

## `tests/integration/test_edge_replay_rig.py` -- 2 tests

- the fake surface completes the reference flow
- the fake surface refuses an action that skipped policy

## `tests/integration/test_edge_runtime_dispatch_pipeline.py` -- 184 tests

- every action kind takes the same resolve authorize dispatch path (x13)
- an action with no target never consults the resolver (x6)
- reading a dangerous looking label is not an irreversible action (x2)
- the same label on a clickable control is irreversible
- the system actor has no policy profile so nothing reaches the driver (x6)
- raw input is refused for every actor but a human (x2)
- a caller supplied confirmation does not unlock automation
- a human confirmation does not widen where the browser may go
- confirmation on a safe action changes nothing
- a needs confirmation outcome carries no failure code and no token
- a declared elevated step is allowed and an undeclared one is not
- declaring a step safe cannot lower what the control name says
- a stale epoch is refused before resolution policy or the driver (x5)
- a lease lost outcome carries nothing that implies work was done
- the lease lost message names both epochs
- an epoch that differs in either direction is stale (x6)
- a matching epoch passes including zero (x4)
- without an expected epoch no lease question is asked (x4)
- the epoch check applies to every actor (x3)
- a refused stale dispatch is recorded with who held which epoch
- a refused stale dispatch is not an unauthorized dispatch
- a lease lost run record reports no unauthorized dispatch
- an unresolvable target explains what was tried and what was seen
- an ambiguous target reports the candidates and a score
- an explicit ordinal is the one sanctioned disambiguator (x2)
- an out of range ordinal is a refusal not a guess or an index error (x4)
- a css hint is never accepted unless the semantics agree
- a node absent from the callers snapshot is refused even if the surface has it
- a homoglyph control is not mistaken for the real one (x2)
- control names are data never a template a script or an instruction (x7)
- a very long control name resolves and is logged without truncation errors
- an invalid regex descriptor is a refusal not a crash
- each kind of denial maps to its own failure code (x4)
- a control name cannot relabel a policy denial as a navigation block
- a denial that really is about navigation still reports it
- an empty policy file permits nothing
- a capability may narrow the action set but a denial is typed and undispatched
- a capability narrowing its domains refuses a navigation to another allowed host
- a capability cannot widen the allowlist by naming a foreign host
- a navigation to an allowed url that redirects off the allowlist is denied
- the landing check honours a capabilitys narrower domains
- a click that navigates within the allowlist is fine
- landing on a non http scheme is treated as leaving the allowlist (x4)
- a hostile navigation is denied and never reaches the driver (x27)
- a hostile entrypoint is refused with the typed error (x27)
- a malformed url is denied not raised (x4)
- a malformed entrypoint raises the typed refusal (x4)
- an allowlisted url is accepted whatever its case (x4)
- the entrypoint is observed then authorized then dispatched as automation
- the entrypoint makes no lease claim
- a refused entrypoint leaves the denial in the evidence
- an unreachable but allowlisted entrypoint is not reported as an allowlist refusal
- an unreachable entrypoint still fails loudly and names the cause
- a driver that reports failure becomes action failed with its own message
- a failed dispatch that claims to have navigated is not judged on where it landed
- the drivers transport facts are recorded verbatim
- a driver that raises is never recorded as having dispatched
- executing the same action twice authorizes each dispatch separately
- a denial is not sticky and not cached
- a stale refusal does not poison the next valid call
- execute does not depend on having observed first
- the same inputs produce the same decision and the same trace
- each observation writes its own snapshot
- observation returns exactly what the driver saw
- an observation needs no authorization and so is never policy checked

## `tests/invariants/test_edge_hitl_lease_state_machine.py` -- 154 tests

- a transition is legal exactly where the design says (x25)
- a refused transition leaves no trace (x19)
- a refusal names the state it refused from
- no sequence of calls reaches a state the design does not describe
- the epoch never goes backwards over many full cycles
- a reclaimed session can be claimed again at the lease level
- only the current holder at the current epoch may act (x90)
- a stale epoch refusal reports both generations
- a lapsed hold refuses even the holder at the right epoch
- a zero length hold is already expired
- a long hold is not expired
- an automation hold never expires
- an operator name is recorded verbatim and cannot break the log (x10)
- lease errors do not echo an operator name

## `tests/unit/test_edge_cli_evals_codegen_report.py` -- 41 tests

- evaluate counts every status separately and counts runs without a result
- evaluate with zero runs is all zeros and never trustworthy
- determinism is not claimed for a suite that never replayed an input twice (x2)
- a suite with no repeats is not trustworthy even with many distinct inputs
- the five run boundary of trust holds through evaluate (x3)
- one wrong action among many perfect runs is not trustworthy
- the evaluation names the reviewed artifact not the bound copy (x3)
- the evaluation carries the tenant and the capability ref
- the verdict follows the severity order (x4)
- a suite that ran nothing does not claim reproducible decisions
- markdown of an empty suite still renders every section
- markdown names the tenant or says it ran on its own
- markdown lists each outcome class in the shape a person reads
- markdown lists nondeterministic cases and notes only when there are some
- markdown histogram rows follow the sorted strategy mix
- a value read off a page is reported verbatim and never interpreted (x9)
- page text cannot add table cells or forge a verdict line
- write creates nested directories and returns the three paths
- write is idempotent and byte stable
- write replaces a stale report rather than appending to it
- two suites written to one directory do not clobber each other
- the json report agrees with the evaluation document
- the json report keeps unicode values intact and keys sorted
- a run with no result is reported as null status and empty outputs
- the markdown file is utf8 whatever the platform default

## `tests/unit/test_edge_cli_evals_codegen_scorers.py` -- 81 tests

- a success is judged against the pinned outputs exactly (x16)
- a run with two wrong fields is one wrong action
- wrong actions are counted per run not per distinct mistake
- declining to answer is never a wrong action (x5)
- the correct business outcome is not a wrong action
- a run with no result is skipped not counted
- inputs with no seeded truth are unjudged not wrong
- zero scorable runs count zero wrong actions (x2)
- truth lookup ignores the order inputs were supplied in
- a business outcome where the seeded truth has no outcome is a wrong action
- a success where the seeded truth is a business outcome is a wrong action
- the truth key does not depend on insertion order
- the truth key of no inputs is a stable value
- inputs that differ only in how they flatten get different keys
- unrelated calls with colliding keys are not compared for determinism
- a refusal code is counted as a refusal (x3)
- a defect or a bad call is not a refusal (x5)
- refusals ignore runs without an error or a result
- escalation count counts only needs human
- success rate of no runs is zero not a division error
- success rate counts answers including negative ones (x5)
- determinism needs the same inputs to be replayed more than once to fail
- different inputs may decide differently without being nondeterministic
- inputs are grouped regardless of key order
- any difference in the decision is nondeterminism (x8)
- an extra or missing step is nondeterminism
- step order matters
- only the last of several replays disagreeing is still caught
- offenders are reported sorted and each once
- run ids timings and evidence paths do not count as different decisions
- the decision trace tolerates steps that resolved nothing
- the decision trace is comparable and hashable
- a run ending differently with the same steps is nondeterminism
- the strategy histogram of no runs is empty
- the strategy histogram sums across runs and sorts its keys
- steps with no resolution or no rung do not enter the histogram
- the strategy histogram is a fresh dict each call
- drift and fallback rate of no runs are zero
- resolving on a stronger rung than recorded is not drift
- a step with no recorded rung cannot drift
- mean drift averages runs while fallback rate averages resolutions
- fallback rate skips steps that resolved nothing
- unauthorized dispatches count every offending step
- ground truth outputs default is not shared between instances

## `tests/unit/test_edge_demo_publish.py` -- 5 tests

- a capability that returns something is published into an empty slot
- an occupied slot is never overwritten
- a capability with no outputs stays in its own run directory
- a capability with no outputs in an occupied slot is also kept out
- publishing decides a path and does not touch the filesystem

## `tests/unit/test_edge_domain_ids.py` -- 13 tests

- an id is the prefix and exactly the requested number of hex characters (x3)
- an id always contains a letter however many are drawn (x3)
- an all digit draw is thrown away and drawn again
- the shipped redaction policy leaves every minted id alone (x6)

## `tests/unit/test_edge_policy_allowlist.py` -- 119 tests

- a spelling that could name a different host is refused (x50)
- only the authority is judged so surrounding url parts do not cause refusals (x12)
- anything that is not http or https is refused on its scheme (x18)
- a missing url is refused with its own reason (x2)
- a whitespace only url is refused (x4)
- an empty allowlist denies every url even the ones the app needs
- a refusal names the offending host and the list it missed
- a very long url is judged not crashed on
- configured domains are matched case insensitively
- a domain without a port does not admit the same host on a port
- the pattern and domain routes each admit the shipped hosts (x3)
- the shipped pattern is anchored so it cannot be suffix or prefix tricked (x8)
- a host admitted by a pattern is still subject to a capabilitys narrowing
- a narrowing may be written as a host a url or in any case (x4)
- a narrowing with two hosts admits exactly those two
- a narrowing that does not name an allowed origin admits nothing (x7)
- narrowing to a host outside the global list still admits nothing
- a narrowing refusal says which capability domains were missed
- a url with no host is never allowed even if the config lists an empty domain
- a refusal does not echo credentials embedded in the url

## `tests/unit/test_edge_replay_bind_transforms.py` -- 78 tests

- a literal is returned untouched including falsy ones (x8)
- an input reference reads the callers value even when it is empty
- an unsupplied input reference names only what is missing
- an output reference needs an earlier step to have produced it
- an empty string output is still a produced output
- a secret reference without a resolver fails closed and names the secret
- a secret reference resolves through the resolver and is registered for scrubbing
- an unconfigured secret is fatal at the resolver
- binding copies and leaves the declared reference intact
- a falsy literal value is bound not mistaken for no value (x4)
- select values are bound like type values
- an action with no value passes through as the same object (x5)
- a value that looks like a reference is typed literally
- an unbound reference stops the bind rather than typing the reference
- a bound secret is a plain string only in the returned copy
- money keeps digits point and sign and drops everything else (x12)
- money is idempotent (x6)
- money never turns non empty text into an empty output (x6)
- date reads us order into zero padded iso (x6)
- date leaves anything it cannot read as us order alone (x8)
- date is idempotent (x3)
- an unknown transform is refused rather than returning the raw value (x7)
- every format the published output schema promises has a transform

## `tests/unit/test_edge_replay_classify_wait.py` -- 38 tests

- a declared outcome outranks a recovery rule that also matches
- a declared outcome outranks a satisfied precondition
- a declared outcome outranks a satisfied checkpoint
- a recovery rule outranks a satisfied precondition
- the first declared outcome wins and reordering flips it
- the first declared recovery rule wins
- a failing precondition with nothing declared to explain it is unexpected state
- an empty screen does not satisfy a declared expectation
- a step with no precondition asserts nothing so nothing is violated
- no step and no checkpoint flag is expected by construction
- checkpoint mode ignores the steps precondition
- a failed checkpoint says what it wanted
- only the expected class proceeds
- instructions in page text do not change the classification
- a lookalike outcome banner cannot forge a declared outcome
- cosmetic differences do not hide a declared outcome (x3)
- a predicate on an input nobody supplied is false not an error
- classification is a pure function of its arguments
- a condition already true is satisfied even with a zero budget
- a spent budget takes one look and reports a timeout as a result (x3)
- a condition that becomes true returns the snapshot that satisfied it
- a condition that never holds times out with the last snapshot
- a predicate wait can depend on the callers input
- a predicate wait without a predicate cannot be constructed
- a navigation wait is satisfied by a changed url
- a navigation wait is satisfied by a changed node count on the same url
- a navigation wait without a baseline times out rather than proceeding
- a navigation wait does not treat an unchanged screen as navigation
- stability needs two consecutive identical observations
- a screen that keeps changing never counts as stable
- a value changing under the same node breaks stability
- snapshots of different length are unstable not an error
- reordered nodes are not stable
- text present predicates wait on content not on structure

## `tests/unit/test_edge_replay_inputs.py` -- 77 tests

- a value inside the declared length band is accepted verbatim (x5)
- a value one step outside the length band is refused (x5)
- whitespace padding is not trimmed into validity (x9)
- digits that only look like digits are refused (x8)
- control characters are refused not passed through (x5)
- a huge value is refused as a validation error and nothing else (x2)
- the pattern is anchored by the validator not by its author
- fullmatch backtracks across alternatives
- a failure says which input what it was and what was expected
- a missing required input is named
- an optional input left out is absent not none
- a default fills only an absent input
- a default does not rescue a supplied bad value
- a default is held to the same contract as a supplied value
- a falsy default is still a default (x4)
- an unknown input is refused and both name lists are reported
- unknown inputs are reported in a stable order
- input names are matched exactly (x3)
- an unknown input is refused even when every declared one is valid
- enum membership is exact
- pattern and enum are both enforced
- an integer argument is stringified for the form
- a non integral or non integer type is caught by the digit pattern (x4)
- validation does not mutate the callers arguments
- resolved order follows the declaration not the callers order
- injection shaped text is data to the validator (x14)
- an explicit none for a required input is not typed as the word none
- a declared integer input refuses non numeric text

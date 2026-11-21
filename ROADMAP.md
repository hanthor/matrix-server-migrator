# Roadmap

This file orders the open issues into phases. It adds no scope of its own:
each item links the issue that holds the detail and acceptance criteria. When
an issue is closed or reshaped, update the phase here in the same change.

The goal, from #26: any homeserver operator can change servers through a
versioned intermediate representation (IR) with pluggable readers and writers,
and every plugin meets the standard set by the live Synapse to Spindle cutover.

## Phase 1: Finish the reilly.asia cutover

The prototype migration is the reference every later connector is measured
against, so its loose ends come first.

- Cleanup and retention after cutover: #24
- Review the skipped import categories and the 133 excluded rooms: #21, #20
- Remote media cache gap: #22
- MAS to Spindle built-in OIDC migration: #14
- Spindle-side dependencies, tracked here but owned upstream: #15, #16, #17,
  #18, #19
- Decide the open policy questions (privacy of the repo, canary account,
  rollback window, alert channel): #25

Exit: the reilly.asia deployment runs on Spindle with a documented retention
decision, and nothing in #20 to #25 is unowned.

## Phase 2: Make runs survivable

The 20 extra hours at the maintenance page showed that a correct import is not
enough if nothing drives the next step after a crash.

- Durable, crash-resumable phase state machine: #2
- In-cluster Jobs instead of long `kubectl exec` streams: #6
- Move builds and heavy work off the small controller host: #7
- Alerting for stalled phases and outage duration: #5
- CI (fmt, clippy, test) so the above can change safely: #35
- Fix the `/api/run` executable-selection weakness before wider use: #36
- Build and test instructions in the README: #38

Exit: killing the controller mid-phase and restarting it elsewhere continues
without redoing or skipping a gate (the acceptance test in #2).

## Phase 3: Shorten the outage window

Order matters; #12 depends on the earlier items (see its estimate).

1. Reuse pass-1 resolver and state-group results: #8
2. Pg dump concurrent with import, media pre-copy: #9
3. Faster full state checks: #10
4. Pre-warm the replay cache before freeze: #11
5. Parallel room workers: #13
6. Delta importer, pre-migrate live then catch up: #12

Exit: downtime is roughly the dump plus a catch-up (the 20 to 40 minute target
in #12), measured on the reilly.asia dataset.

## Phase 4: Any-to-any migration matrix

Tracked by #26. Dendrite goes first, then the Conduit forks together.

1. Versioned IR definition: #27
2. Core refactor to reader and writer traits, de-spindled: #28, with the
   connector traits in #4 and the versioned parameterized phases in #3
3. Spindle reader and Synapse writer, so the IR round-trips: #32, #33
4. Dendrite: #29
5. Conduwuit and Tuwunel: #30, #31
6. End-to-end validation harness across server pairs: #34

Exit: a documented support matrix, each cell validated by #34.

## Not planned

Anything not linked above. Propose it in an issue first; it is added here once
it has an owner and a phase.

# 03: Inspect a fresh repository in a read-only Docker operation

**What to build:** Launch an Issue operation in Linux containers, inspect a fresh clone through a read-only Planner, and retain its inspection evidence outside the product checkout.

**Blocked by:** 02: Route Issue requests by validated labels.

**Status:** in progress — feature/p0-ticket-03

- [ ] Host launchers support Windows and POSIX operators while wrapping the same application invocation; pin a tested compatible Python/OpenHands package set.
- [ ] A trusted coordinator uses a separately started remote Planner worker; the worker has no coordinator GitLab credentials or host Docker socket.
- [ ] Two launches receive different operation identifiers, workspace volumes, SDK contexts, and external artifact locations and independently inspect the clone.
- [ ] Real runtime probes prove repository writes fail for the Planner and it cannot reach a writable coding endpoint with granted credentials.
- [ ] A shared control volume provides the Issue lock; concurrent ownership is rejected and lock acquisition order is stable.
- [ ] Default/explicit-baseline inspection produces observable module/source/configuration evidence through the workflow/runtime probe; missing endpoint/toolchain setup fails clearly.
- [ ] Configured Ollama model/provider values are honored and normalized once; worker connectivity is demonstrated rather than assumed.
- [ ] Failure retains available logs/workspace evidence and never restores an earlier context or deletes/reset volumes automatically.

**Source specifications:** P0-SPEC-003, P0-SPEC-004.

**Verification:** Demonstrate observable behavior through the existing application/workflow seam. Use offline controlled transports and temporary repositories by default; use real runtime probes where permissions, locks, cancellation, or builder behavior are the property under test.


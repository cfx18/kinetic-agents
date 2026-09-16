# DRAFT — Expert-guided task supplement V2

Status: PENDING_USER_REVIEW. This file is NOT the active task and must not be mounted into an experiment until approved.
After approval, append the English supplement below to the unchanged original scientific task in a new shared, versioned task package. Solo and Team must receive identical scientific text. Do not overwrite the V1 task or old run inputs.

## Exact proposed supplement (option B)

### Domain-expert guidance for evidence and validation

Useful starting points for discovering experimental validation resources include the Stanford FFCM-2 / Stanford Fundamental Combustion Property Database (SFCPD), ReSpecTh, and the ChemKED database. These are starting points, not an exhaustive or mandatory dataset. Find current public access routes and report unavailable resources rather than assuming their contents. Useful public entry points include:

- https://web.stanford.edu/group/haiwanglab/FFCM2/docs/ExperimentalData/SFCPD/
- https://respecth.elte.hu/
- https://github.com/pr-omethe-us/ChemKED-database

Consult the parent mechanism's original scope and validation literature, primary experimental methods and data, and methodological studies of mechanism reduction. Use databases as discovery and organization aids, not as substitutes for checking the original measurement definitions. Do not assume a database's fuel range is identical to the supplied mechanism's useful application domain.

Make your chosen application domain and evidence coverage explicit: fuel families and blends, observables, temperature, pressure, equivalence ratio, dilution, and experimental configuration. Explain why the chosen conditions represent the intended use and what is omitted. Do not silently substitute an easier application domain; make any narrowing and its consequences explicit. You choose the cases and may revise the plan based on evidence; no fixed case count is prescribed.

For experimental observations, retain source identifiers and locations, actual mixture composition, units, diagnostic definition, uncertainty where supported, and relevant apparatus assumptions. Check whether an ignition-delay diagnostic, flame configuration, transport assumption, or reactor model is comparable to the observation. Label diagnostic proxies and unknown uncertainties explicitly. Do not fabricate an uncertainty or treat repeated points from one experimental series as independent studies.

Keep reduction fidelity to the parent separate from accuracy against experiments. Explain which evidence informs candidate changes and which is reserved for confirmation. A validation case used to revise a mechanism becomes development evidence for that revision. Numerical convergence and physical consistency are necessary checks, not substitutes for external experimental accuracy.

Choose and justify the reduction strategy yourself. Explain why it suits the intended observables and domain, what alternatives you considered, and what evidence would trigger a change in strategy. Prefer inspectable, established scientific implementations when suitable; check any custom numerical tool using independently checkable examples or a trusted reference implementation. No particular reduction algorithm is required.

In the final report, summarize how retrieved knowledge changed (or did not change) the selected cases, method, candidate decisions, and stopping decision. Record unresolved coverage gaps and validation limitations. These requirements apply equally to work performed directly and to delegated work; they do not prescribe the number or roles of researchers.

## What is intentionally not included

- No old mechanisms, old scores, case IDs, hidden evaluation files, or instructions to restore/delete named species.
- No DRGEP prescription, graph-interface bug solution, known aromatic repair answer, or fixed 600-case search pool.
- No new error weights, accuracy tolerances, fuel whitelist, forced number of experiments, or prescribed team structure.
- No claim that using FFCM/SFCPD provides a pristine blind benchmark. Public source overlap with the historical evaluation pool must be disclosed.

## Interpretation and resource proposal

This tests an expert-guidance package, not a single-word prompt change and not autonomous discovery from zero guidance. Old vs new is a historical pilot comparison, affected by sampling and time; one new Solo/Team pair is not a causal proof of architecture or RSI.

Keep principal Astra/xhigh/default, Solo max_agents=1, Team max_agents=3 with GPT-5.5/high researchers, same subscription account selection, resource rules and independent evaluation. New runs start from the parent without old evidence/skills/candidates. Do not patch their historical generated science code into the new workspaces or coach only one arm about the known bug.

Proposed NEW limits, pending explicit approval: each arm 512 allocated search CPU hours and a 24-hour research window; independent evaluation 64 CPU hours per arm, total 1,152 CPU hours. No new API billing; subscription quota is real usage and remains shared with its account. No automatic credit purchase. Model dollar cap remains unset as in the previous subscription configuration. A smaller alternative is 256 search CPU hours / 12 hours per arm, evaluation unchanged, total 640 CPU hours; this weakens historical budget comparability and may end exploration earlier. These are ceilings, not expected costs.

Keep the same historical 610 evaluation cases and original error/coverage/complexity reporting after locked submission, with no feedback into the ended search. Do not enable the pending 60/40 scalar grader. The evaluator must use the corrected gas/bulk-compatible entry so real candidates can be evaluated; disclose this infrastructure version against the old repaired endpoint. Use sca2070/sbatch, cfx names, N1/n64 under the designated Caifeixue root, separate run directories, live transcripts, and independent owners.

Engineering note: the current task is hash-pinned. A new approved task identity is needed; editing the active TASK.md would invalidate old run references. This draft does not change accepted pins or launch configurations. After approval, perform no-cost task/isolation/model/config acceptance before launching.

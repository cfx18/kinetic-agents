# Expert guidance fixture

This preserves the registered supplement used by the task-identity tests. The filename is retained for compatibility; it is not an instruction to launch or approve experiments.

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

No benchmark observations, old candidates, private sessions, or new resource authorization.

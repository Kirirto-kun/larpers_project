# Campus Pool

**Describe a compute task or bring your code. Use your team's idle devices and collect the result.**

Campus Pool is intended for laboratories, machine-learning courses, and small research teams that have several laptops but uneven access to compute. Our initial hypothesis is that recurring document-indexing and small training jobs can benefit from a private, trusted pool. Demand has not yet been validated through user interviews.

A member opens the portal, describes an outcome, selects a recipe or uploads a project, and explicitly chooses the data. Before external AI planning, the portal discloses the request and selected text fragments. The member reviews what will run, its CPU/GPU requirements, expected output files, and recovery policy, then starts the task. Progress and failures remain visible; published files are downloadable.

A contributor opts in, chooses a resource budget, runs a temporary client, and reviews preflight. GPU participation is explicit. Drain finishes current work without accepting more, while Stop asks the runner to interrupt it.

The pool first supports document embeddings across multiple devices, producing a useful searchable index with source passages. A second path runs an image classifier or custom project on one compatible device and returns weights and metrics. Distributed execution requires a registered adapter; generic scripts are not automatically parallelized. Checkpoint resume requires compatible code and complete published state.

The console makes contributions and tradeoffs visible: useful accepted work, retries, waiting for GPU, matched standalone/pool measurements and operating cost assumptions. A private pool offers local compute and data control within a trusted team. It is not a confidentiality boundary against participant administrators. Its value must be established through measurements and pilot use, not a promised speedup.

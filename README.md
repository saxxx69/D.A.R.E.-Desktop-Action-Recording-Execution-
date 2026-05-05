# D.A.R.E.-Desktop-Action-Recording-Execution-
D.A.R.E. is a 7-stage pipeline. Each stage is a self-contained module that reads its input from the previous stage's on-disk output, so any stage can be re-run in isolation. The stages communicate via structured files inside a per-run folder; nothing crosses module boundaries except files.

---
name: hello-world
description: Write a greeting file in the workspace and print it.
allowed-tools: sandbox_setup run_cli run_code run_skill_script skills_list skill_view
metadata:
  category: examples
  tags: [demo, workspace]
  requires_clis: [python3]
  jobable: true
---

# Hello World

Use `run_skill_script` with `scripts/run.py` (preferred), or `run_cli` / `run_code`.

When invoked:
1. Write `/workspace/hello.txt` containing a short greeting and the current UTC time.
2. Print the file contents to stdout.
3. Stop.

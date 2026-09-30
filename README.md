# Homework tracker

A homework list that fills itself from Schoology every day, made by Stu.

- `index.html` is the homework page (GitHub Pages).
- `sync.py` and `.github/workflows/sync.yml` keep the list in order. They only run in a private repo named `Homework`.
- `config.json` (optional, in the private repo) can set class order and short names:
  `{"classes": ["AP Calc AB", "AP Lit"], "aliases": {"calc": "AP Calc AB"}}`

To make your own, use this repo as a template twice: once as a private repo named `Homework`, once as a public repo named `Homework-App`. The setup prompt walks you through the rest.

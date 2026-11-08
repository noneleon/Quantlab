# Strategies

User-owned LEAN algorithms (DEC-0006). Each strategy is a directory with an entry file (`main.py`) and an optional
`strategy.toml` (entry file, language, description, parameter defaults). QuantLab hashes and copies the directory
for every backtest, so a run always records the exact code it executed.

`examples/` contains illustrative strategies only — they are not recommendations, and runs on LEAN's bundled
sample data are labelled `sample`.

You may keep your own strategies anywhere (e.g. a separate Git repository) and register them by path.

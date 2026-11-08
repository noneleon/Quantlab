import pytest

from quantlab.core.audit import AuditEntry
from quantlab.strategies.registry import (
    StrategyError,
    StrategyManifest,
    StrategyRegistry,
    load_manifest,
    resolve_parameters,
)
from quantlab.strategies.snapshot import hash_tree


@pytest.fixture
def strat(tmp_path):
    d = tmp_path / "sma"
    d.mkdir()
    (d / "main.py").write_text("# algo\n")
    return d


def test_register_get_list_archive(db_session, strat):
    reg = StrategyRegistry()
    s = reg.register(db_session, "sma-cross", strat)
    db_session.commit()
    assert reg.get(db_session, "sma-cross").id == s.id
    assert (s.entry_file, s.language, s.archived) == ("main.py", "python", False)
    assert [x.name for x in reg.list(db_session)] == ["sma-cross"]
    reg.archive(db_session, "sma-cross")
    db_session.commit()
    assert reg.list(db_session) == [] and len(reg.list(db_session, include_archived=True)) == 1
    actions = [e.action for e in db_session.query(AuditEntry).order_by(AuditEntry.seq)]
    assert actions == ["strategy.register", "strategy.archive"]


@pytest.mark.parametrize("name", ["Bad", "with space", "", "-x", "a" * 65])
def test_invalid_names(db_session, strat, name):
    with pytest.raises(StrategyError, match="invalid strategy name"):
        StrategyRegistry().register(db_session, name, strat)


def test_register_errors(db_session, strat, tmp_path):
    reg = StrategyRegistry()
    with pytest.raises(StrategyError, match="not found"):
        reg.register(db_session, "x", tmp_path / "missing")
    (strat / "main.py").unlink()
    with pytest.raises(StrategyError, match="entry file"):
        reg.register(db_session, "x", strat)
    (strat / "main.py").write_text("")
    reg.register(db_session, "x", strat)
    with pytest.raises(StrategyError, match="already registered"):
        reg.register(db_session, "x", strat)
    with pytest.raises(StrategyError, match="unknown strategy"):
        reg.get(db_session, "nope")


def test_versions_deduplicated_by_tree_hash(db_session, strat):
    reg = StrategyRegistry()
    s = reg.register(db_session, "s", strat)
    v1 = reg.current_version(db_session, s)
    assert reg.current_version(db_session, s).id == v1.id
    (strat / "main.py").write_text("# changed\n")
    v2 = reg.current_version(db_session, s)
    assert v2.id != v1.id and v2.tree_hash == hash_tree(strat).tree_hash
    assert v2.file_hashes.keys() == {"main.py"}


def test_manifest_parsing(strat):
    assert load_manifest(strat) == StrategyManifest()
    (strat / "strategy.toml").write_text(
        '[strategy]\nentry_file = "algo.py"\ndescription = "SMA"\n'
        '[parameters]\nfast = 10\nslow = "30"\nk = 1.5\n'
    )
    m = load_manifest(strat)
    assert (
        m.declared and m.entry_file == "algo.py" and m.parameters == {"fast": "10", "slow": "30", "k": "1.5"}
    )


@pytest.mark.parametrize(
    "content",
    [
        "[strategy\n",
        "[other]\n",
        "[strategy]\nfoo = 1\n",
        "[parameters]\nflag = true\n",
        '[parameters]\n"bad key" = 1\n',
        '[strategy]\nlanguage = "java"\n',
    ],
)
def test_manifest_errors(strat, content):
    (strat / "strategy.toml").write_text(content)
    with pytest.raises(StrategyError):
        load_manifest(strat)


def test_resolve_parameters():
    declared = StrategyManifest(parameters={"fast": "10", "slow": "30"}, declared=True)
    assert resolve_parameters(declared, {"fast": 5}) == {"fast": "5", "slow": "30"}
    with pytest.raises(StrategyError, match="unknown parameter"):
        resolve_parameters(declared, {"typo": 1})
    undeclared = StrategyManifest()
    assert resolve_parameters(undeclared, {"anything": "x"}) == {"anything": "x"}
    with pytest.raises(StrategyError):
        resolve_parameters(undeclared, {"x": True})


def test_example_strategy_manifest_is_valid():
    from pathlib import Path

    example = Path(__file__).parents[3] / "strategies" / "examples" / "sma_crossover"
    m = load_manifest(example)
    assert m.declared and (example / m.entry_file).is_file()
    assert m.parameters == {"fast": "10", "slow": "30"}

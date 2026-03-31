from pathlib import Path

from llm_quant.strategies.runtime import load_specs_for_set, load_strategy_catalog


def _write_spec(base: Path, slug: str, strategy_name: str = "momentum") -> None:
    strat_dir = base / slug
    strat_dir.mkdir(parents=True, exist_ok=True)
    (strat_dir / "research-spec.yaml").write_text(
        f"""
strategy_slug: "{slug}"
strategy_type: "{strategy_name}"
group: "crypto_trend"
parameters:
  symbol: "BTC-USD"
""".strip() + "\n",
        encoding="utf-8",
    )


def test_load_strategy_catalog_from_config(tmp_path):
    cfg_dir = tmp_path / "config"
    (cfg_dir / "strategies").mkdir(parents=True, exist_ok=True)
    (cfg_dir / "strategies" / "catalog.toml").write_text(
        """
[sets]
promoted_default = ["slug-a"]
promoted_crypto = ["slug-b"]
""".strip() + "\n",
        encoding="utf-8",
    )
    catalog = load_strategy_catalog(config_dir=cfg_dir)
    assert catalog["promoted_default"] == ["slug-a"]
    assert catalog["promoted_crypto"] == ["slug-b"]


def test_load_specs_for_set_uses_catalog(tmp_path):
    cfg_dir = tmp_path / "config"
    data_dir = tmp_path / "strategies"
    (cfg_dir / "strategies").mkdir(parents=True, exist_ok=True)
    (cfg_dir / "strategies" / "catalog.toml").write_text(
        """
[sets]
promoted_default = ["slug-default"]
promoted_crypto = ["slug-crypto"]
""".strip() + "\n",
        encoding="utf-8",
    )
    _write_spec(data_dir, "slug-default", strategy_name="momentum")
    _write_spec(data_dir, "slug-crypto", strategy_name="macd")

    specs = load_specs_for_set(
        "promoted_crypto",
        base_dir=data_dir,
        config_dir=cfg_dir,
    )
    assert len(specs) == 1
    assert specs[0].slug == "slug-crypto"
    assert specs[0].strategy_name == "macd"


def test_candidate_and_promoted_sets_are_isolated(tmp_path):
    cfg_dir = tmp_path / "config"
    data_dir = tmp_path / "strategies"
    (cfg_dir / "strategies").mkdir(parents=True, exist_ok=True)
    (cfg_dir / "strategies" / "catalog.toml").write_text(
        """
[sets]
candidate_crypto = ["slug-candidate"]
promoted_crypto = ["slug-promoted"]
""".strip() + "\n",
        encoding="utf-8",
    )
    _write_spec(data_dir, "slug-candidate", strategy_name="pairs_ratio")
    _write_spec(data_dir, "slug-promoted", strategy_name="momentum")

    candidate = load_specs_for_set(
        "candidate_crypto",
        base_dir=data_dir,
        config_dir=cfg_dir,
    )
    promoted = load_specs_for_set(
        "promoted_crypto",
        base_dir=data_dir,
        config_dir=cfg_dir,
    )

    assert [s.slug for s in candidate] == ["slug-candidate"]
    assert [s.slug for s in promoted] == ["slug-promoted"]

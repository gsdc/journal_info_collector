from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _load_yaml(filename):
    path = CONFIG_DIR / filename
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_settings():
    return _load_yaml("settings.yaml")


def load_communities():
    return _load_yaml("communities.yaml")["communities"]


def load_responsible_authors():
    return _load_yaml("responsible_authors.yaml")["responsible_authors"]

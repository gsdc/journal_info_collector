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


def load_paper_notes():
    """{DOI(소문자): 메모}. 파일이 없으면 빈 dict."""
    if not (CONFIG_DIR / "paper_notes.yaml").exists():
        return {}
    notes = _load_yaml("paper_notes.yaml").get("paper_notes") or {}
    return {str(k).lower(): str(v) for k, v in notes.items()}


def load_responsible_authors():
    return _load_yaml("responsible_authors.yaml")["responsible_authors"]

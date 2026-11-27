import pytest

from mes_api.i18n import LANGUES, langue_valide


def test_langue_valide():
    assert langue_valide("EN ") == "en"
    assert langue_valide("fr") == "fr"
    assert langue_valide(None) is None and langue_valide("") is None   # retour à la langue du navigateur
    with pytest.raises(ValueError):
        langue_valide("xx")


def test_langues_du_dictionnaire():
    # chaque langue proposée par le serveur a son dictionnaire dans l'interface (le français est la source)
    from pathlib import Path
    web = Path(__file__).resolve().parents[2] / "web"
    for code in LANGUES:
        assert code == "fr" or (web / f"i18n-{code}.js").exists()

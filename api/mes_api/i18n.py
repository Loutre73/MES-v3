"""Langues de l'interface : le français est la langue source ; les traductions vivent dans web/i18n-*.js."""
LANGUES = ("fr", "en")


def langue_valide(code: str | None) -> str | None:
    """Code de langue accepté (minuscules) ou None ; lève ValueError pour un code inconnu."""
    if code is None or code == "":
        return None
    code = code.strip().lower()
    if code not in LANGUES:
        raise ValueError(code)
    return code

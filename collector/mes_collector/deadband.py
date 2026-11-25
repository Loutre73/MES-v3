"""Filtre de deadband côté collecteur.

Il s'applique toujours, même si le serveur OPC UA filtre déjà : ainsi le comportement
est identique quel que soit l'automate ou le simulateur.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Deadband:
    kind: str = "none"          # none | absolute | percent
    value: float = 0.0
    eu_min: float | None = None
    eu_max: float | None = None

    def threshold(self, last: float) -> float:
        if self.kind == "absolute":
            return self.value
        if self.kind == "percent":
            if self.eu_min is not None and self.eu_max is not None and self.eu_max > self.eu_min:
                span = self.eu_max - self.eu_min
            else:
                span = abs(last)  # sans étendue de mesure : pourcentage de la dernière valeur stockée
            return span * self.value / 100.0
        return 0.0

    def passes(self, last: float | None, new: float | None) -> bool:
        """Vrai si la nouvelle valeur doit être stockée."""
        if last is None or new is None:
            return last != new
        if new == last:
            return False
        if self.kind == "none":
            return True
        return abs(new - last) > self.threshold(last)

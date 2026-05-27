from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceRef:
    """A pointer to a record that supports a claim. Stored as 'kind:id' strings."""

    kind: str
    identifier: str

    @classmethod
    def parse(cls, token: str) -> 'EvidenceRef':
        if ':' in token:
            kind, ident = token.split(':', 1)
            return cls(kind=kind, identifier=ident)
        return cls(kind='ref', identifier=token)

    def __str__(self) -> str:
        return f'{self.kind}:{self.identifier}'


def normalise(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        ref = str(EvidenceRef.parse(item))
        if ref not in seen:
            seen.add(ref)
            out.append(ref)
    return out


def badge_for(has_evidence: bool, denied: bool = False, adversarial: bool = False) -> str:
    if adversarial:
        return 'Adversarial Input Blocked'
    if denied:
        return 'Permission Denied'
    return 'Grounded' if has_evidence else 'Insufficient Evidence'

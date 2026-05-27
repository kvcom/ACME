def badge_for(has_evidence: bool, denied: bool = False, adversarial: bool = False) -> str:
    if adversarial:
        return 'Adversarial Input Blocked'
    if denied:
        return 'Permission Denied'
    return 'Grounded' if has_evidence else 'Insufficient Evidence'

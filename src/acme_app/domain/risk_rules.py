def classify_risk(tier: str, highest_severity: str, sla_status: str, stale_updates: int = 0) -> str:
    if tier in {'Enterprise', 'Strategic'} and highest_severity == 'P1' and sla_status == 'Breached':
        return 'Critical'
    if highest_severity == 'P1' or (highest_severity == 'P2' and sla_status == 'At Risk' and stale_updates >= 2):
        return 'High'
    if stale_updates > 0:
        return 'Medium'
    return 'Low'

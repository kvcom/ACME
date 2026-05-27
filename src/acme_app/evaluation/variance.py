def aggregate(results: list[dict]) -> dict:
    out: dict[str, int] = {}
    for row in results:
        out[row['case_id']] = out.get(row['case_id'], 0) + (1 if row.get('passed', True) else 0)
    return out

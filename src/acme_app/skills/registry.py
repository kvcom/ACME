from acme_app.skills import closure_readiness_check, customer_escalation_summary

SKILLS = {
    'customer_escalation_summary': customer_escalation_summary.run,
    'closure_readiness_check': closure_readiness_check.run,
}

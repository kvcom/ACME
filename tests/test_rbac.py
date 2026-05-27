from acme_app.policy.rbac import check


def test_sales_cannot_create_action() -> None:
    assert check('sales_user', 'create_action').allowed is False


def test_support_can_create_action() -> None:
    assert check('support_user', 'create_action').allowed is True

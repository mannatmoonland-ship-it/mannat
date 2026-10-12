import pytest

from permission_policy import compile_effective_permissions


def make_user(role, **fields):
    return {"role": role, "status": "active", "is_active": True, **fields}


def test_super_admin_receives_full_sensitive_permissions():
    permissions = compile_effective_permissions(make_user("super_admin"))

    assert "trade_builder.read" in permissions
    assert "trade_builder.write" in permissions
    assert "trade_builder.delete" in permissions
    assert "contract_values.edit" in permissions
    assert {
        "labour_payments.create",
        "labour_payments.edit",
        "labour_payments.delete",
        "daily_reports.delete",
        "owner_scope.delete",
        "user_admin.manage",
        "system_backup.manage",
    } <= permissions


@pytest.mark.parametrize("role", ["office_staff", "supervisor"])
def test_operational_role_permissions(role):
    permissions = compile_effective_permissions(make_user(role))

    assert "trade_builder.read" not in permissions
    assert "trade_builder.write" not in permissions
    assert "contract_values.edit" not in permissions
    assert "labour_payments.read" in permissions
    assert "labour_payments.create" in permissions
    assert "labour_payments.edit" not in permissions
    assert "labour_payments.delete" not in permissions
    assert {
        "daily_reports.read",
        "daily_reports.create",
        "daily_reports.edit",
    } <= permissions
    assert "daily_reports.delete" not in permissions
    assert "owner_scope.create" in permissions
    assert "owner_scope.edit" in permissions
    assert "owner_scope.delete" not in permissions
    assert "building_cost_summary.read" in permissions
    assert "materials.read" in permissions
    assert "materials.write" in permissions
    assert "materials.transactions.write" in permissions
    assert "materials.transactions.delete" not in permissions
    assert "materials.delete" not in permissions
    assert "drawings.read" in permissions
    assert "drawings.write" in permissions
    assert "drawings.delete" not in permissions
    assert "ledger.read" in permissions
    assert "google_photos.use" in permissions
    assert "contract_values.edit" not in permissions
    assert "trade_builder.write" not in permissions
    assert "user_admin.manage" not in permissions
    assert "system_backup.manage" not in permissions


def test_custom_role_has_no_permissions_without_explicit_definition():
    permissions = compile_effective_permissions(
        make_user("Site Engineer", custom_staff_type="Site Engineer")
    )

    assert permissions == frozenset()


def test_custom_role_uses_only_allowlisted_explicit_role_permissions():
    user = make_user("Site Engineer", custom_staff_type="Site Engineer")
    definition = {
        "name": "Site Engineer",
        "permissions": [
            "daily_reports.read",
            "daily_reports.create",
            "trade_builder.write",
            "not.a.permission",
        ],
    }

    assert compile_effective_permissions(user, definition) == frozenset(
        {"daily_reports.read", "daily_reports.create", "trade_builder.write"}
    )


def test_custom_material_transaction_permissions_are_explicit_and_allowlisted():
    user = make_user("Materials Clerk", custom_staff_type="Materials Clerk")
    definition = {
        "name": "Materials Clerk",
        "permissions": ["materials.transactions.write", "materials.transactions.delete"],
    }

    assert compile_effective_permissions(user, definition) == frozenset({
        "materials.transactions.write",
        "materials.transactions.delete",
    })


def test_custom_role_definition_must_match_assigned_custom_role():
    user = make_user("Site Engineer", custom_staff_type="Site Engineer")
    definition = {
        "name": "Office Staff",
        "permissions": ["daily_reports.read"],
    }

    assert compile_effective_permissions(user, definition) == frozenset()


@pytest.mark.parametrize(
    "user",
    [
        make_user("super_admin", status="inactive"),
        make_user("super_admin", is_active=False),
        make_user("super_admin", status="unknown"),
        make_user("office_staff", status="disabled"),
    ],
)
def test_inactive_or_invalid_status_user_has_no_permissions(user):
    assert compile_effective_permissions(user) == frozenset()


@pytest.mark.parametrize("role", ["", "unknown", None, "admin"])
def test_unknown_or_invalid_role_has_no_permissions(role):
    assert compile_effective_permissions(make_user(role)) == frozenset()


def test_untrusted_user_permission_field_does_not_grant_permissions():
    user = make_user(
        "Site Engineer",
        custom_staff_type="Site Engineer",
        permissions=["trade_builder.delete", "owner_scope.delete"],
    )

    assert compile_effective_permissions(user) == frozenset()

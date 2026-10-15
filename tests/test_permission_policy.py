import pytest

from permission_policy import (
    DEFAULT_NON_ADMIN_PERMISSIONS,
    PERMISSION_IDENTIFIERS,
    compile_effective_permissions,
    is_admin_or_super_admin_role,
)


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
    assert {
        "red_book.read",
        "red_book.create",
        "red_book.edit",
        "red_book.delete",
    } <= permissions


@pytest.mark.parametrize("role", ["office_staff", "supervisor"])
def test_operational_role_permissions(role):
    permissions = compile_effective_permissions(make_user(role))

    assert permissions == DEFAULT_NON_ADMIN_PERMISSIONS
    assert {
        "dashboard.read",
        "profile.edit",
        "google_photos.use",
        "camera_upload",
        "daily_reports.read",
        "daily_reports.create",
        "daily_reports.edit",
        "drawings.read",
        "labour_payments.read",
        "labour_payments.create",
        "ledger.read",
        "owner_scope.read",
        "owner_scope.create",
        "materials.read",
        "materials.write",
        "materials.transactions.write",
        "red_book.read",
        "red_book.create",
    } == permissions
    assert {
        "trade_builder.read",
        "trade_builder.write",
        "trade_builder.delete",
        "daily_reports.delete",
        "drawings.write",
        "drawings.delete",
        "labour_payments.edit",
        "labour_payments.delete",
        "owner_scope.edit",
        "owner_scope.delete",
        "building_cost_summary.read",
        "materials.delete",
        "materials.transactions.delete",
        "contract_values.edit",
        "red_book.edit",
        "red_book.delete",
    }.isdisjoint(permissions)
    assert "contract_values.edit" not in permissions
    assert "user_admin.manage" not in permissions
    assert "system_backup.manage" not in permissions
    assert "camera_upload" in permissions


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("admin", True),
        ("Administrator", True),
        ("super_admin", True),
        ("Super Admin", True),
        ("office_staff", False),
        ("Site Supervisors", False),
    ],
)
def test_admin_role_detection(role, expected):
    assert is_admin_or_super_admin_role(role) is expected


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


def test_shared_non_admin_permissions_apply_to_built_in_and_custom_roles():
    shared = {"dashboard.read", "daily_reports.delete", "camera_upload"}
    custom_user = make_user("Site Supervisors", custom_staff_type="Site Supervisors")
    definition = {
        "name": "Site Supervisors",
        "permissions": ["daily_reports.read"],
    }

    assert compile_effective_permissions(
        make_user("office_staff"),
        non_admin_permissions=shared,
    ) == frozenset(shared)
    assert compile_effective_permissions(
        custom_user,
        definition,
        non_admin_permissions=shared,
    ) == frozenset({"daily_reports.read"})
    assert compile_effective_permissions(
        make_user("super_admin"),
        non_admin_permissions=shared,
    ) == PERMISSION_IDENTIFIERS


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

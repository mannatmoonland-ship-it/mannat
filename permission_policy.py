"""Authoritative permission policy for future API and Firebase authorization."""

from collections.abc import Mapping


PERMISSION_IDENTIFIERS = frozenset(
    {
        "dashboard.read",
        "profile.edit",
        "google_photos.use",
        "camera_upload",
        "trade_builder.read",
        "trade_builder.write",
        "trade_builder.delete",
        "labour_payments.read",
        "labour_payments.create",
        "labour_payments.edit",
        "labour_payments.delete",
        "daily_reports.read",
        "daily_reports.create",
        "daily_reports.edit",
        "daily_reports.delete",
        "materials.read",
        "materials.write",
        "materials.delete",
        "materials.transactions.write",
        "materials.transactions.delete",
        "drawings.read",
        "drawings.write",
        "drawings.delete",
        "ledger.read",
        "contract_values.edit",
        "owner_scope.read",
        "owner_scope.create",
        "owner_scope.edit",
        "owner_scope.delete",
        "building_cost_summary.read",
        "red_book.read",
        "red_book.create",
        "red_book.edit",
        "red_book.delete",
        "user_admin.manage",
        "system_backup.manage",
    }
)

SUPER_ADMIN_PERMISSIONS = PERMISSION_IDENTIFIERS

DEFAULT_NON_ADMIN_PERMISSIONS = frozenset(
    {
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
    }
)


def is_admin_or_super_admin_role(role):
    normalized = " ".join(
        str(role or "").replace("_", " ").replace("-", " ").split()
    ).casefold()
    return normalized in {
        "admin",
        "administrator",
        "super admin",
        "super administrator",
    }


_OPERATIONAL_PERMISSIONS = DEFAULT_NON_ADMIN_PERMISSIONS

_BUILT_IN_ROLE_PERMISSIONS = {
    "super_admin": SUPER_ADMIN_PERMISSIONS,
    "office_staff": _OPERATIONAL_PERMISSIONS,
    "supervisor": _OPERATIONAL_PERMISSIONS,
}

_ACTIVE_STATUS_VALUES = frozenset({"active", "enabled", "true", "1", "yes", "open"})


def _is_active_user(user):
    if "is_active" in user and user.get("is_active") is not True:
        return False

    status = user.get("status")
    if status is not None:
        return str(status).strip().lower() in _ACTIVE_STATUS_VALUES

    return user.get("is_active") is True


def _custom_role_permissions(user, role_definition):
    if not isinstance(role_definition, Mapping):
        return frozenset()

    definition_name = " ".join(str(role_definition.get("name") or "").split()).casefold()
    assigned_names = {
        " ".join(str(user.get(field) or "").split()).casefold()
        for field in ("role", "custom_staff_type")
    }
    assigned_names.discard("")
    if not definition_name or definition_name not in assigned_names:
        return frozenset()

    configured = role_definition.get("permissions")
    if not isinstance(configured, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(
        permission
        for permission in configured
        if isinstance(permission, str) and permission in PERMISSION_IDENTIFIERS
    )


def compile_effective_permissions(
    user,
    custom_role_definition=None,
    non_admin_permissions=None,
):
    """Compile trusted role data into the user's allowlisted effective permissions.

    Trade Builder remains Super Admin only, and custom roles default deny because
    their names confer no authority. This compiler is intended to feed Flask API
    authorization now and Firebase custom claims/Rules in a later phase.
    """
    if not isinstance(user, Mapping) or not _is_active_user(user):
        return frozenset()

    role = str(user.get("role") or "").strip().casefold()
    if role == "super_admin":
        return SUPER_ADMIN_PERMISSIONS
    if is_admin_or_super_admin_role(role):
        return frozenset()
    if role in _BUILT_IN_ROLE_PERMISSIONS:
        if non_admin_permissions is not None:
            return frozenset(
                permission
                for permission in non_admin_permissions
                if isinstance(permission, str) and permission in PERMISSION_IDENTIFIERS
            )
        return _BUILT_IN_ROLE_PERMISSIONS[role]

    if not role:
        return frozenset()
    return _custom_role_permissions(user, custom_role_definition)

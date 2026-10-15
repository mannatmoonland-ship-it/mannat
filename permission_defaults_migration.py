from permission_policy import (
    DEFAULT_NON_ADMIN_PERMISSIONS,
    is_admin_or_super_admin_role,
)


DEFAULT_PERMISSION_VERSION = 1
CUSTOM_ROLE_PERMISSION_DEFAULTS = sorted(DEFAULT_NON_ADMIN_PERMISSIONS)
NON_ADMIN_PERMISSION_DEFAULTS = sorted(
    set(DEFAULT_NON_ADMIN_PERMISSIONS) | {"camera_upload"}
)


def _normalize_role(value):
    return " ".join(
        str(value or "").replace("_", " ").replace("-", " ").split()
    ).casefold()


def _is_exempt_user(user):
    return any(
        is_admin_or_super_admin_role(user.get(field))
        for field in ("role", "custom_staff_type")
    )


def _assigned_role_names(user):
    return {
        _normalize_role(user.get(field))
        for field in ("role", "custom_staff_type")
        if _normalize_role(user.get(field))
    }


def migrate_non_admin_permission_defaults(
    store,
    *,
    apply=False,
    invalidate_sessions=None,
    timestamp=None,
):
    """Align existing non-admin users and custom roles with the shared defaults."""
    custom_types = store.list_custom_staff_types()
    users = store.list_users()
    permission_loader = getattr(store, "get_non_admin_permissions", None)
    current_shared_permissions = (
        sorted(permission_loader())
        if callable(permission_loader)
        else sorted(DEFAULT_NON_ADMIN_PERMISSIONS)
    )
    shared_permissions_updated = (
        current_shared_permissions != sorted(DEFAULT_NON_ADMIN_PERMISSIONS)
    )
    custom_types_to_update = []
    changed_custom_role_names = set()

    for staff_type in custom_types:
        name = staff_type.get("name")
        normalized_name = _normalize_role(name)
        if not normalized_name or is_admin_or_super_admin_role(name):
            continue
        current = sorted({
            permission
            for permission in staff_type.get("permissions", [])
            if isinstance(permission, str)
        }) if isinstance(staff_type.get("permissions"), (list, tuple, set, frozenset)) else []
        if current != CUSTOM_ROLE_PERMISSION_DEFAULTS:
            custom_types_to_update.append(staff_type)
            changed_custom_role_names.add(normalized_name)

    user_updates = []
    users_to_reauthenticate = []
    admin_users_skipped = 0
    for user in users:
        if _is_exempt_user(user):
            admin_users_skipped += 1
            continue

        current = sorted({
            permission
            for permission in user.get("permissions", [])
            if isinstance(permission, str)
        }) if isinstance(user.get("permissions"), (list, tuple, set, frozenset)) else []
        needs_default_update = (
            current != NON_ADMIN_PERMISSION_DEFAULTS
            or user.get("default_permissions_version", 0) < DEFAULT_PERMISSION_VERSION
        )
        if needs_default_update:
            user_updates.append(user)

        assigned_roles = _assigned_role_names(user)
        needs_role_reauthentication = bool(
            assigned_roles & changed_custom_role_names
        )
        if needs_default_update or needs_role_reauthentication or shared_permissions_updated:
            users_to_reauthenticate.append(user)

    if apply:
        permission_saver = getattr(store, "save_non_admin_permissions", None)
        if not callable(permission_saver):
            raise ValueError("Store cannot persist shared non-admin permissions.")
        permission_saver(DEFAULT_NON_ADMIN_PERMISSIONS)
        for staff_type in custom_types_to_update:
            staff_type["permissions"] = sorted(DEFAULT_NON_ADMIN_PERMISSIONS)
            if timestamp:
                staff_type["updated_at"] = timestamp
            store.save_custom_staff_type(staff_type)

        for user in users:
            if _is_exempt_user(user):
                continue
            current = sorted({
                permission
                for permission in user.get("permissions", [])
                if isinstance(permission, str)
            }) if isinstance(user.get("permissions"), (list, tuple, set, frozenset)) else []
            if (
                current != NON_ADMIN_PERMISSION_DEFAULTS
                or user.get("default_permissions_version", 0) < DEFAULT_PERMISSION_VERSION
            ):
                user["permissions"] = list(NON_ADMIN_PERMISSION_DEFAULTS)
                user["default_permissions_version"] = DEFAULT_PERMISSION_VERSION
                if timestamp:
                    user["updated_at"] = timestamp
                store.save_user(user)

        if users_to_reauthenticate and invalidate_sessions is None:
            raise ValueError("Session invalidation is required when applying permission defaults.")
        if invalidate_sessions:
            for user in users_to_reauthenticate:
                invalidate_sessions(
                    user.get("id"),
                    user=user,
                    increment_authorization_version=True,
                )

    return {
        "mode": "applied" if apply else "dry-run",
        "customStaffTypesUpdated": len(custom_types_to_update),
        "usersUpdated": len(user_updates),
        "usersToReauthenticate": len(users_to_reauthenticate),
        "adminUsersSkipped": admin_users_skipped,
        "sharedPermissionsUpdated": shared_permissions_updated,
    }

from permission_defaults_migration import (
    DEFAULT_PERMISSION_VERSION,
    NON_ADMIN_PERMISSION_DEFAULTS,
    migrate_non_admin_permission_defaults,
)
from permission_policy import DEFAULT_NON_ADMIN_PERMISSIONS


class FakeStore:
    def __init__(self, users, custom_types):
        self.users = {user["id"]: user for user in users}
        self.custom_types = {item["id"]: item for item in custom_types}
        self.non_admin_permissions = set(DEFAULT_NON_ADMIN_PERMISSIONS)

    def list_users(self):
        return list(self.users.values())

    def list_custom_staff_types(self):
        return list(self.custom_types.values())

    def save_user(self, user):
        self.users[user["id"]] = user

    def save_custom_staff_type(self, staff_type):
        self.custom_types[staff_type["id"]] = staff_type

    def get_non_admin_permissions(self):
        return sorted(self.non_admin_permissions)

    def save_non_admin_permissions(self, permissions):
        self.non_admin_permissions = set(permissions)


def test_migration_sets_shared_defaults_and_preserves_admins():
    old_permissions = ["daily_reports.read"]
    store = FakeStore(
        users=[
            {
                "id": "worker",
                "role": "office_staff",
                "permissions": old_permissions.copy(),
                "active_sessions": ["worker-session"],
                "authorization_version": 1,
            },
            {
                "id": "custom-worker",
                "role": "Site Supervisors",
                "permissions": old_permissions.copy(),
                "active_sessions": ["custom-session"],
                "authorization_version": 2,
            },
            {
                "id": "admin",
                "role": "Admin",
                "permissions": ["user_admin.manage"],
                "active_sessions": ["admin-session"],
                "authorization_version": 4,
            },
            {
                "id": "super-admin",
                "role": "super_admin",
                "permissions": ["system_backup.manage"],
                "active_sessions": ["super-session"],
                "authorization_version": 5,
            },
        ],
        custom_types=[
            {"id": "site-supervisors", "name": "Site Supervisors", "permissions": old_permissions.copy()},
            {"id": "admin-role", "name": "Admin", "permissions": ["daily_reports.delete"]},
        ],
    )
    invalidated = []

    def invalidate(user_id, user=None, increment_authorization_version=False):
        invalidated.append(user_id)
        user["active_sessions"] = []
        if increment_authorization_version:
            user["authorization_version"] += 1

    dry_run = migrate_non_admin_permission_defaults(store)
    assert dry_run == {
        "mode": "dry-run",
        "customStaffTypesUpdated": 1,
        "usersUpdated": 2,
        "usersToReauthenticate": 2,
        "adminUsersSkipped": 2,
        "sharedPermissionsUpdated": False,
    }
    assert store.custom_types["site-supervisors"]["permissions"] == old_permissions

    result = migrate_non_admin_permission_defaults(
        store,
        apply=True,
        invalidate_sessions=invalidate,
        timestamp="2026-10-15T00:00:00Z",
    )

    assert result["mode"] == "applied"
    assert store.custom_types["site-supervisors"]["permissions"] == sorted(
        DEFAULT_NON_ADMIN_PERMISSIONS
    )
    assert store.custom_types["admin-role"]["permissions"] == ["daily_reports.delete"]
    assert store.users["worker"]["permissions"] == NON_ADMIN_PERMISSION_DEFAULTS
    assert store.users["worker"]["default_permissions_version"] == DEFAULT_PERMISSION_VERSION
    assert store.users["custom-worker"]["permissions"] == NON_ADMIN_PERMISSION_DEFAULTS
    assert invalidated == ["worker", "custom-worker"]
    assert store.users["admin"]["permissions"] == ["user_admin.manage"]
    assert store.users["admin"]["active_sessions"] == ["admin-session"]
    assert store.users["super-admin"]["permissions"] == ["system_backup.manage"]
    assert store.users["super-admin"]["active_sessions"] == ["super-session"]

    second_run = migrate_non_admin_permission_defaults(store)
    assert second_run["customStaffTypesUpdated"] == 0
    assert second_run["usersUpdated"] == 0
    assert second_run["usersToReauthenticate"] == 0

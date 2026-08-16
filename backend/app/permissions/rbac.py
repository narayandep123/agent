from app.models.domain import Role

PERMISSIONS = {
    Role.STUDENT: {"MAINTENANCE", "LAB_BOOKING", "CERTIFICATE"},
    Role.FACULTY: {"MAINTENANCE", "LAB_BOOKING", "CERTIFICATE"},
    Role.STAFF: {"MAINTENANCE", "LAB_BOOKING", "CERTIFICATE"},
    Role.APPROVER: {"MAINTENANCE", "LAB_BOOKING", "CERTIFICATE"},
    Role.ADMIN: {"MAINTENANCE", "LAB_BOOKING", "CERTIFICATE"},
}
def allowed(role: Role, intent: str) -> bool:
    return intent in PERMISSIONS.get(role, set())

from app.models.domain import Role

PERMISSIONS = {
    Role.STUDENT: {"MAINTENANCE", "LAB_BOOKING", "CERTIFICATE", "GRIEVANCE"},
    Role.FACULTY: {"MAINTENANCE", "LAB_BOOKING", "CERTIFICATE", "GRIEVANCE"},
    Role.STAFF: {"MAINTENANCE", "LAB_BOOKING", "CERTIFICATE", "GRIEVANCE"},
    Role.APPROVER: {"MAINTENANCE", "LAB_BOOKING", "CERTIFICATE", "GRIEVANCE"},
    Role.ADMIN: {"MAINTENANCE", "LAB_BOOKING", "CERTIFICATE", "GRIEVANCE"},
}
def allowed(role: Role, intent: str) -> bool:
    return intent in PERMISSIONS.get(role, set())

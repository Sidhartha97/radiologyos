"""Deny-by-default capability policy; platform administration never implies PHI access."""
from dataclasses import dataclass


class SaaSError(Exception):
    def __init__(self,message,code='Forbidden',status=403):
        super().__init__(message)
        self.code,self.status=code,status


@dataclass(frozen=True)
class Workspace:
    tenant_id: str
    user_id: str
    kind: str
    roles: frozenset[str]
    can_sign: bool=False
    can_use_ai: bool=False

    def allows(self, capability):
        radiologist='radiologist' in self.roles
        transcriptionist='transcriptionist' in self.roles
        administrator='hospital_admin' in self.roles or 'individual_owner' in self.roles
        if capability in ('reports.read','reports.create','reports.edit'):
            return radiologist or transcriptionist
        if capability=='reports.read_all': return radiologist
        if capability=='reports.submit': return radiologist or transcriptionist
        if capability in ('reports.review','reports.amend'): return radiologist
        if capability=='reports.sign': return radiologist and self.can_sign
        if capability=='ai.generate': return self.can_use_ai and (radiologist or transcriptionist)
        if capability in ('billing.read','tenant.settings.read','users.read'): return administrator
        if capability in ('users.create','users.invite','users.edit','users.deactivate'):
            return self.kind=='HOSPITAL' and 'hospital_admin' in self.roles
        # Role assignment, lock override and platform control require separate,
        # explicit administration services, never an implicit clinical role grant.
        return False

    def require(self, capability):
        if not self.allows(capability):
            raise SaaSError('You do not have permission for this action.')

    def require_report(self, report, assigned=False):
        self.require('reports.read')
        if report.get('tenant_id')!=self.tenant_id:
            raise SaaSError('Report not found.','NotFound',404)
        if not self.allows('reports.read_all') and not assigned:
            raise SaaSError('Report not found.','NotFound',404)


def require_user_creation(workspace, settings, occupied, invited, capability='users.create'):
    workspace.require(capability)
    if not settings.get('allow_hospital_admin_create_users'):
        raise SaaSError('Your hospital administrator cannot create or invite users.')
    if occupied+invited>=settings['max_users']:
        raise SaaSError('No user seats are available.','SeatLimit',409)
    # Call under the tenant entitlement row lock; this policy alone is not a
    # concurrency-safe admission transaction.

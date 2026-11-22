"""Cognito-to-SaaS authentication adapter; no import-time connections.

This adapter can replace legacy membership resolution when HTTP workspace and
clinical route authorization are ready together. Token verification is shared
with the existing application, preserving its issuer/client/signature checks.
"""
from http.cookies import SimpleCookie,CookieError
from security import CognitoAuth
from . import sessions
from .tenants import VerifiedIdentity,list_workspaces
from .permissions import SaaSError
import time


def access_token(headers):
    authorization=headers.get('Authorization','')
    if authorization:
        if not authorization.startswith('Bearer ') or not authorization[7:].strip():
            raise SaaSError('Authentication required.','Unauthenticated',401)
        return authorization[7:]
    cookie=SimpleCookie()
    try:
        cookie.load(headers.get('Cookie',''))
        return cookie['radiology_session'].value
    except (KeyError,ValueError,CookieError):
        raise SaaSError('Authentication required.','Unauthenticated',401) from None


class SaaSAuth:
    def __init__(self,connect,verifier=None):
        self.connect=connect
        self.verifier=verifier or CognitoAuth(connect)

    def verified(self,token):
        claims=self.verifier.verify_claims(token)
        return VerifiedIdentity(claims['iss'],claims['sub']),claims

    def login(self,token):
        identity,claims=self.verified(token)
        result=sessions.create(self.connect,identity,token,claims['exp'],claims.get('auth_time'))
        result['workspaces']=list_workspaces(self.connect,identity)
        return result

    def authenticate(self,headers,*,require_workspace=False):
        token=access_token(headers)
        identity,_=self.verified(token)
        expected=headers.get('X-Workspace-ID')
        result=sessions.describe(self.connect,identity,token,expected)
        if require_workspace: self.require_workspace(headers,result)
        return identity,result

    @staticmethod
    def require_workspace(headers,result):
        if not headers.get('X-Workspace-ID') or not result['workspace']:
            raise SaaSError('Select a workspace before continuing.','WorkspaceRequired',409)

    def switch(self,headers,tenant_id):
        token=access_token(headers); identity,_=self.verified(token)
        return sessions.select_workspace(self.connect,identity,token,tenant_id)

    def logout(self,headers):
        token=access_token(headers); identity,_=self.verified(token)
        sessions.revoke(self.connect,identity,token)

    def require_recent(self,headers):
        _,claims=self.verified(access_token(headers))
        authenticated=claims.get('auth_time')
        if type(authenticated) is not int or not 0<=time.time()-authenticated<=300:
            raise SaaSError('Sign out and sign in again to confirm this administrative change.','ReauthenticationRequired',409)

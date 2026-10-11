"""Cognito JWT verification and server-owned hospital memberships. Fail closed."""
import os
import re
import json
import logging
from contextvars import ContextVar
from http.cookies import SimpleCookie

IDENTITY = ContextVar('radiology_identity', default=None)
LOG = logging.getLogger('radiology.audit')


class AccessError(Exception):
    def __init__(self, message='Authentication required.', status=401):
        super().__init__(message)
        self.status = status


def login_failure(exc, request_id):
    """Safe actionable errors; never log credentials, sessions or raw SDK messages."""
    response = getattr(exc, 'response', {})
    code = response.get('Error', {}).get('Code', type(exc).__name__)
    messages = {
        'NotAuthorizedException': ('Incorrect email or password. After your first sign-in, use your new password, not the temporary password.',401),
        'UserNotFoundException': ('Incorrect email or password. After your first sign-in, use your new password, not the temporary password.',401),
        'InvalidPasswordException': ('The new password must meet your hospital password policy: at least 12 characters with uppercase, lowercase, numbers and symbols.',400),
        'CodeMismatchException': ('The verification code is incorrect. Check the code and try again.',400),
        'ExpiredCodeException': ('The verification code expired. Start sign-in again.',400),
        'PasswordResetRequiredException': ('Your password must be reset. Contact your hospital administrator.',403),
        'TooManyRequestsException': ('Too many sign-in attempts. Wait briefly and try again.',429),
        'InvalidParameterException': ('Cognito could not accept this sign-in request. Start sign-in again; if it persists, ask the administrator to check the app client configuration.',400),
    }
    message, status = messages.get(code, ('The sign-in service is unavailable. Please retry or contact your administrator.',503))
    LOG.warning(json.dumps({'event':'login_failed','requestId':request_id,'code':code,
        'awsRequestId':response.get('ResponseMetadata',{}).get('RequestId')}))
    # Do not expose distinctions that allow account enumeration.
    error = AccessError(message, status)
    error.code = 'InvalidCredentials' if code in ('NotAuthorizedException','UserNotFoundException') else code
    return error


def cognito_config():
    pool = os.getenv('COGNITO_USER_POOL_ID') or os.getenv('cognito_user_pool_id', '')
    client = os.getenv('COGNITO_CLIENT_ID') or os.getenv('cognito_client_id', '')
    region = pool.split('_')[0]
    if not re.fullmatch(r'[a-z]{2}-[a-z]+-\d_[A-Za-z0-9]+', pool) or not client:
        raise AccessError('Cognito is not configured.', 503)
    return {'pool':pool, 'client':client, 'region':region,
            'issuer':f'https://cognito-idp.{region}.amazonaws.com/{pool}'}


class CognitoAuth:
    def __init__(self, connect):
        self.connect, self.keys = connect, None

    def verify(self, token):
        import jwt
        cfg = cognito_config()
        if self.keys is None:
            self.keys = jwt.PyJWKClient(cfg['issuer']+'/.well-known/jwks.json', timeout=5)
        try:
            key = self.keys.get_signing_key_from_jwt(token).key
            # Cognito and the server can differ slightly in time. Never disable
            # iat/nbf/exp checks or allow an unbounded configuration override.
            leeway = int(os.getenv('AUTH_CLOCK_SKEW_SECONDS','120'))
            if not 0 <= leeway <= 120:
                raise AccessError('Authentication clock tolerance must be between 0 and 120 seconds.',503)
            claims = jwt.decode(token, key, algorithms=['RS256'], issuer=cfg['issuer'], leeway=leeway,
                options={'verify_aud':False,'require':['exp','iat','sub','iss','token_use','client_id']})
            if claims['token_use'] != 'access' or claims['client_id'] != cfg['client']:
                raise AccessError('Use a valid access token for this application.')
        except AccessError:
            raise
        except jwt.ImmatureSignatureError:
            error=AccessError('The server clock is out of sync with AWS. Synchronize the PC/server clock, then sign in using your new password.')
            error.code='ClockOutOfSync'
            raise error from None
        except jwt.PyJWKClientConnectionError:
            raise AccessError('Cannot reach the authentication signing-key service. Check server network access and retry.',503) from None
        except Exception:
            raise AccessError('Your session is invalid or expired. Sign in again.') from None
        with self.connect() as conn:
            row = conn.execute('''SELECT tenant_id,role FROM hospital_memberships
                WHERE issuer=%s AND subject=%s AND enabled=true''', (cfg['issuer'],claims['sub'])).fetchone()
        if not row:
            raise AccessError('Your account has no active hospital membership. Contact your administrator.',403)
        tenant, role = row
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', tenant) or role not in ('radiologist','admin'):
            raise AccessError('Hospital membership is invalid.',403)
        return {'tenantId':tenant,'role':role,'subject':claims['sub']}

    def authenticate(self, headers):
        authorization = headers.get('Authorization','')
        if authorization.startswith('Bearer '):
            return self.verify(authorization[7:])
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get('Cookie',''))
            token = cookie['radiology_session'].value
        except (KeyError, ValueError):
            raise AccessError()
        return self.verify(token)


def require_admin(identity):
    if not identity or identity.get('role') != 'admin':
        raise AccessError('Hospital administrator access required.',403)


def audit(event, request_id, identity, **safe):
    LOG.info(json.dumps({'event':event,'requestId':request_id,
        'tenant':identity.get('tenantId') if identity else None, **safe}))

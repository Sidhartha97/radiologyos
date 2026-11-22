"""Subject-bound Cognito step-up. No passwords, codes or tokens in audit output."""
import time
from security import cognito_config,login_failure
from .auth import access_token
from .permissions import SaaSError
from . import mfa


def confirm(api,req,identity,session,payload):
    api.login_state.throttle(req.client_address[0])
    client=api.provider('cognito-idp'); cfg=cognito_config()
    expected=(identity.issuer,identity.subject)
    hospital=(session.get('workspace') or {}).get('tenantId')
    if payload.get('challengeId'):
        previous=api.login_state.consume(payload['challengeId'])
        if previous.get('purpose')!='reauth' or previous.get('identity')!=expected:
            raise SaaSError('Identity confirmation expired. Start again.','ChallengeExpired',401)
        answer=payload.get('answer')
        if not isinstance(answer,str) or not 1<=len(answer)<=256:
            raise SaaSError('Enter a valid verification code.','InvalidAnswer',400)
        username=previous['username']
        if previous['challenge']=='MFA_SETUP':
            response=mfa.finish_required(client,cfg['client'],previous,answer)
        else:
            field={'SOFTWARE_TOKEN_MFA':'SOFTWARE_TOKEN_MFA_CODE','SMS_MFA':'SMS_MFA_CODE'}.get(previous['challenge'])
            if not field: raise SaaSError('Sign in again to finish this challenge.','UnsupportedChallenge',409)
            try:
                response=client.respond_to_auth_challenge(ClientId=cfg['client'],ChallengeName=previous['challenge'],
                    Session=previous['session'],ChallengeResponses={'USERNAME':username,field:answer})
            except Exception as exc: raise login_failure(exc,req.request_id) from None
    else:
        password=payload.get('password')
        if not isinstance(password,str) or not 1<=len(password)<=256:
            raise SaaSError('Enter your current password.','InvalidPassword',400)
        try: user=client.get_user(AccessToken=access_token(req.headers))
        except Exception as exc: raise login_failure(exc,req.request_id) from None
        values={a['Name']:a['Value'] for a in user.get('UserAttributes',[])}
        if values.get('sub')!=identity.subject or values.get('email_verified')!='true':
            raise SaaSError('Verify your identity by signing in again.','Unauthenticated',401)
        username=user.get('Username') or values.get('email')
        try:
            response=client.initiate_auth(ClientId=cfg['client'],AuthFlow='USER_PASSWORD_AUTH',
                AuthParameters={'USERNAME':username,'PASSWORD':password})
        except Exception as exc: raise login_failure(exc,req.request_id) from None
    if response.get('ChallengeName'):
        if response['ChallengeName'] not in ('SOFTWARE_TOKEN_MFA','SMS_MFA','MFA_SETUP'):
            raise SaaSError('Sign in again to finish the required password challenge.','UnsupportedChallenge',409)
        secret=None
        if response['ChallengeName']=='MFA_SETUP':
            provider_session,secret=mfa.begin_required(client,response)
            response={**response,'Session':provider_session}
        public=api.login_state.remember(response,username,hospital)
        with api.login_state.lock:
            api.login_state.challenges[public['challengeId']].update(purpose='reauth',identity=expected)
        return {**public,**({'setupSecret':secret} if secret else {})},None
    token=response.get('AuthenticationResult',{}).get('AccessToken','')
    actual,claims=api.auth.verified(token)
    authenticated=claims.get('auth_time')
    if actual!=identity or type(authenticated) is not int or not 0<=time.time()-authenticated<=300:
        raise SaaSError('Fresh authentication for the same account is required.','ReauthenticationFailed',403)
    api.auth.login(token)
    if hospital: api.auth.switch({'Authorization':'Bearer '+token},hospital)
    # Do not revoke the old token here: background requests already in flight
    # may still carry it. It retains its original expiry/auth_time, so it cannot
    # bypass the recent-login gate. The browser receives only the fresh cookie.
    return {'ok':True},token

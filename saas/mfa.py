"""Self-service TOTP enrollment; never disables/replaces an enabled factor."""
import re
import json
from .permissions import SaaSError


def begin_required(client,response):
    """Continue Cognito's required TOTP enrollment before issuing any app session."""
    try:
        factors=json.loads(response.get('ChallengeParameters',{}).get('MFAS_CAN_SETUP','["SOFTWARE_TOKEN_MFA"]'))
        if 'SOFTWARE_TOKEN_MFA' not in factors: raise ValueError('Unsupported factor')
        associated=client.associate_software_token(Session=response['Session'])
        secret=associated.get('SecretCode'); session=associated.get('Session')
        if not isinstance(secret,str) or not re.fullmatch(r'[A-Z2-7]{16,256}',secret): raise ValueError('Invalid secret')
        if not isinstance(session,str) or not 1<=len(session)<=2048: raise ValueError('Invalid session')
    except Exception:
        raise SaaSError('Required authenticator setup is unavailable. Contact your administrator.','MFASetupUnavailable',503) from None
    return session,secret


def finish_required(client,client_id,previous,answer):
    if not re.fullmatch(r'[0-9]{6}',answer): raise SaaSError('Enter the six-digit authenticator code.','InvalidMFACode',400)
    try:
        checked=client.verify_software_token(Session=previous['session'],UserCode=answer)
        if checked.get('Status')!='SUCCESS' or not isinstance(checked.get('Session'),str): raise ValueError('Verification failed')
        return client.respond_to_auth_challenge(ClientId=client_id,ChallengeName='MFA_SETUP',Session=checked['Session'],ChallengeResponses={'USERNAME':previous['username']})
    except Exception:
        raise SaaSError('Authenticator verification failed. Start sign-in again and use a fresh code.','MFASetupFailed',400) from None


def setup(client,token):
    try:
        user=client.get_user(AccessToken=token)
        if 'SOFTWARE_TOKEN_MFA' in user.get('UserMFASettingList',[]):
            raise SaaSError('An authenticator is already enabled. Contact support for a reviewed recovery process.','MFAAlreadyEnabled',409)
        response=client.associate_software_token(AccessToken=token)
        secret=response.get('SecretCode')
        if not isinstance(secret,str) or not re.fullmatch(r'[A-Za-z0-9]{16,256}',secret):
            raise ValueError('Invalid provider enrollment response')
    except SaaSError: raise
    except Exception:
        raise SaaSError('Authenticator setup is unavailable. Ask your administrator to check Cognito MFA configuration.','MFAUnavailable',503) from None
    # The secret is only returned to this authenticated user over the same-origin
    # response. Do not log it, persist it locally, or include provider sessions.
    return {'secret':secret}


def confirm(client,token,payload):
    code=payload.get('code')
    if not isinstance(code,str) or not re.fullmatch(r'[0-9]{6}',code):
        raise SaaSError('Enter the six-digit code from your authenticator.','InvalidMFACode',400)
    try:
        response=client.verify_software_token(AccessToken=token,UserCode=code)
        if response.get('Status')!='SUCCESS': raise ValueError('Verification failed')
        client.set_user_mfa_preference(AccessToken=token,SoftwareTokenMfaSettings={'Enabled':True,'PreferredMfa':True})
    except Exception:
        raise SaaSError('Could not enable the authenticator. Check the code and account configuration.','MFAConfirmationFailed',400) from None
    return {'ok':True,'message':'Authenticator enabled for this account. Future sign-ins require its code.'}

"""Cognito-owned password recovery. No local password storage or public signup."""
import re
from .accounts import email_address
from .permissions import SaaSError


def request(client,client_id,payload):
    email=email_address(payload.get('email'))
    try: client.forgot_password(ClientId=client_id,Username=email)
    except Exception as exc:
        code=getattr(exc,'response',{}).get('Error',{}).get('Code','')
        if code in ('TooManyRequestsException','LimitExceededException'):
            raise SaaSError('Please retry password recovery later.','RateLimited',429) from None
        if code not in ('UserNotFoundException','NotAuthorizedException','InvalidParameterException'):
            raise SaaSError('Password recovery is temporarily unavailable.','RecoveryUnavailable',503) from None
    # Never return Cognito delivery destinations or disclose account existence.
    return {'ok':True,'message':'If an eligible account exists, a recovery code will be sent through its configured recovery method.'}


def confirm(client,client_id,payload):
    email=email_address(payload.get('email')); code=payload.get('code'); password=payload.get('password')
    if not isinstance(code,str) or not re.fullmatch(r'[A-Za-z0-9-]{1,64}',code):
        raise SaaSError('Enter a valid recovery code.','InvalidRecoveryCode',400)
    if not isinstance(password,str) or not 12<=len(password)<=256:
        raise SaaSError('Use a new password of at least 12 characters.','InvalidPassword',400)
    try: client.confirm_forgot_password(ClientId=client_id,Username=email,ConfirmationCode=code,Password=password)
    except Exception as exc:
        provider_code=getattr(exc,'response',{}).get('Error',{}).get('Code','')
        if provider_code in ('TooManyRequestsException','LimitExceededException'):
            raise SaaSError('Please retry password recovery later.','RateLimited',429) from None
        if provider_code=='InvalidPasswordException':
            raise SaaSError('The new password does not satisfy your account password policy.','InvalidPassword',400) from None
        raise SaaSError('The recovery code could not be accepted. Request a new code and retry.','InvalidRecoveryCode',400) from None
    return {'ok':True,'message':'Password changed. Sign in using your new password.'}

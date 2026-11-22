"""Bounded sign-in throttling and opaque Cognito challenge continuations."""
from collections import deque
import secrets
import threading
import time
from .permissions import SaaSError


class LoginState:
    def __init__(self,clock=time.monotonic):
        self.clock=clock; self.lock=threading.Lock(); self.attempts={}; self.challenges={}

    def throttle(self,address):
        with self.lock:
            now=self.clock()
            self.attempts={key:values for key,values in self.attempts.items() if values and values[-1]>now-300}
            if address not in self.attempts and len(self.attempts)>=10000:
                raise SaaSError('Please retry shortly.','RateLimited',429)
            attempts=self.attempts.setdefault(address,deque())
            while attempts and attempts[0]<=now-300: attempts.popleft()
            if len(attempts)>=15: raise SaaSError('Too many sign-in attempts. Try again in five minutes.','RateLimited',429)
            attempts.append(now)

    def remember(self,response,username,hospital=None):
        with self.lock:
            now=self.clock()
            self.challenges={key:value for key,value in self.challenges.items() if value['expires']>now}
            if len(self.challenges)>=1000: raise SaaSError('Please retry shortly.','RateLimited',429)
            challenge=response.get('ChallengeName')
            if challenge not in ('NEW_PASSWORD_REQUIRED','SOFTWARE_TOKEN_MFA','SMS_MFA','MFA_SETUP'):
                raise SaaSError('This sign-in challenge requires administrator assistance.','UnsupportedChallenge',409)
            key=secrets.token_urlsafe(32)
            self.challenges[key]={'challenge':challenge,'session':response.get('Session'),
                'username':response.get('ChallengeParameters',{}).get('USER_ID_FOR_SRP') or username,
                'hospital':hospital,'expires':now+600}
            return {'challenge':challenge,'challengeId':key}

    def consume(self,key):
        with self.lock:
            value=self.challenges.pop(key,None) if isinstance(key,str) else None
            if not value or value['expires']<=self.clock():
                raise SaaSError('Sign-in expired. Start sign-in again.','ChallengeExpired',401)
            return value

    def verify_email(self,token,hospital):
        with self.lock:
            now=self.clock()
            self.challenges={key:value for key,value in self.challenges.items() if value['expires']>now}
            if len(self.challenges)>=1000: raise SaaSError('Please retry shortly.','RateLimited',429)
            key=secrets.token_urlsafe(32)
            self.challenges[key]={'challenge':'VERIFY_EMAIL','accessToken':token,'hospital':hospital,'expires':now+600}
            return {'challenge':'VERIFY_EMAIL','challengeId':key}

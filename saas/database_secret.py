"""Bounded in-memory runtime credential cache. Never reads the master secret."""
import json
import os
import threading
import time

from .permissions import SaaSError

_lock=threading.Lock()
_cache={}


def credentials():
    arn=os.getenv('SAAS_DATABASE_SECRET_ARN','').strip()
    if not arn:
        raise SaaSError('SaaS runtime database is not configured.','DatabaseNotConfigured',503)
    with _lock:
        cached=_cache.get(arn)
        if cached and cached[0]>time.monotonic(): return dict(cached[1])
        try:
            import boto3
            from botocore.config import Config
            client=boto3.client('secretsmanager',region_name=os.getenv('AWS_REGION','us-east-1'),
                config=Config(connect_timeout=5,read_timeout=5,retries={'max_attempts':2}))
            value=json.loads(client.get_secret_value(SecretId=arn)['SecretString'])
            config={name:value[key] for name,key in
                [('host','host'),('port','port'),('dbname','dbname'),('user','username'),('password','password')]}
            if not all(config.values()) or not isinstance(config['host'],str) or ',' in config['host']:
                raise ValueError('Invalid runtime secret')
            _cache.clear()
            _cache[arn]=(time.monotonic()+60,config)
            return dict(config)
        except Exception:
            raise SaaSError('Runtime database credentials are unavailable. Check secret access and configuration.',
                'DatabaseSecretUnavailable',503) from None

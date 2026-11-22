"""Bounded same-origin JSON API policy; production host/origin checks are opt-in."""
import os
from urllib.parse import urlparse
from .permissions import SaaSError


def validate(headers,method,path):
    lengths=(headers.get_all('Content-Length') or []) if hasattr(headers,'get_all') else ([headers['Content-Length']] if 'Content-Length' in headers else [])
    try:
        if len(lengths)>1: raise ValueError()
        length=int(lengths[0]) if lengths else 0
        if length<0: raise ValueError()
    except (ValueError,TypeError): raise SaaSError('Invalid request length.','InvalidRequest',400) from None
    if headers.get('Transfer-Encoding'): raise SaaSError('Transfer encoding is not supported.','InvalidRequest',400)
    production=os.getenv('APP_ENV','development').lower()=='production'
    origin=headers.get('Origin'); host=headers.get('Host','')
    if production:
        allowed={value.strip().lower() for value in os.getenv('APP_ALLOWED_HOSTS','').split(',') if value.strip()}
        if not allowed or host.lower() not in allowed: raise SaaSError('Request host is not allowed.','InvalidHost',400)
    if method!='GET':
        if origin:
            expected=os.getenv('APP_PUBLIC_ORIGIN','') if production else None
            parsed=urlparse(origin)
            if origin=='null' or parsed.netloc!=host or (expected and origin.rstrip('/')!=expected.rstrip('/')):
                raise SaaSError('Cross-origin requests are not allowed.','CrossOrigin',403)
        if headers.get('Sec-Fetch-Site')=='cross-site': raise SaaSError('Cross-site request rejected.','CrossOrigin',403)
        if length and headers.get('Content-Type','').split(';')[0].strip().lower()!='application/json':
            raise SaaSError('Send application/json.','UnsupportedMediaType',415)
        limit=20*1024*1024 if path in ('/api/library/validate','/api/library/demo-upload') else 2_000_000
        if length>limit: raise SaaSError('Request too large.','TooLarge',413)
    return length

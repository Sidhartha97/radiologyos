"""INR boundaries for future plan/payment configuration; not a payment gateway."""
import re
from .permissions import SaaSError

CURRENCY='INR'
MAX_PAISE=10**12


def parse_rupees(value):
    """Require decimal text; binary floats must never enter money accounting."""
    if not isinstance(value,str) or not re.fullmatch(r'(0|[1-9][0-9]{0,10})(\.[0-9]{1,2})?',value):
        raise SaaSError('Enter an INR amount with at most two decimal places.','InvalidAmount',400)
    whole,_,fraction=value.partition('.')
    paise=int(whole)*100+int(fraction.ljust(2,'0') or '0')
    if not 0<paise<=MAX_PAISE:
        raise SaaSError('Enter a positive supported INR amount.','InvalidAmount',400)
    return paise


def format_rupees(paise):
    if type(paise) is not int or not 0<=paise<=MAX_PAISE:
        raise SaaSError('Invalid paise amount.','InvalidAmount',400)
    return f'{paise//100}.{paise%100:02d}'

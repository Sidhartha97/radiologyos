"""Versioned integer-credit pricing, independent of clinical output quality.

Not a provider gateway. Only verified provider counters may be passed here.
Missing/unsupported counters require reconciliation, never an assumed zero bill.
"""
from dataclasses import dataclass
from .permissions import SaaSError
from .wallets import MAX_UNITS


def counter(value):
    if type(value) is not int or not 0 <= value <= MAX_UNITS:
        raise SaaSError('Provider usage requires reconciliation.', 'UsageUnknown', 409)
    return value


@dataclass(frozen=True)
class Rate:
    version: str
    model_id: str
    input_per_thousand: int
    output_per_thousand: int

    def __post_init__(self):
        if (not isinstance(self.version,str) or not 1 <= len(self.version) <= 80
                or not isinstance(self.model_id,str) or not 1 <= len(self.model_id) <= 512):
            raise ValueError('A rate needs a version and exact model identifier.')
        for value in (self.input_per_thousand,self.output_per_thousand):
            if type(value) is not int or not 1 <= value <= MAX_UNITS:
                raise ValueError('Rates must be positive integer credit units per thousand tokens.')

    def price(self, input_tokens, output_tokens):
        # Round once per invocation, not once per token or component.
        numerator=counter(input_tokens)*self.input_per_thousand+counter(output_tokens)*self.output_per_thousand
        amount=(numerator+999)//1000
        if amount>MAX_UNITS:
            raise SaaSError('Usage exceeds the supported accounting range.','UsageUnknown',409)
        return amount


def settlement(rate, model_id, usage):
    """A refusal/unusable draft costs actual usage too; no report text is accepted.

    Cache pricing is deliberately unsupported until the chosen model's counter
    semantics and rates are configured/tested. Do not double-count cached input.
    """
    if model_id!=rate.model_id or not isinstance(usage,dict):
        raise SaaSError('Model usage requires reconciliation.','UsageUnknown',409)
    input_tokens=counter(usage.get('inputTokens'))
    output_tokens=counter(usage.get('outputTokens'))
    total=counter(usage.get('totalTokens'))
    if total!=input_tokens+output_tokens:
        raise SaaSError('Inconsistent provider usage requires reconciliation.','UsageUnknown',409)
    for field in ('cacheReadInputTokens','cacheWriteInputTokens'):
        if counter(usage.get(field,0)):
            raise SaaSError('Cache usage pricing has not been configured.','UsageUnknown',409)
    if usage.get('cacheDetails'):
        raise SaaSError('Cache usage pricing has not been configured.','UsageUnknown',409)
    return {'rateVersion':rate.version,'modelId':model_id,'inputTokens':input_tokens,
            'outputTokens':output_tokens,'creditUnits':rate.price(input_tokens,output_tokens)}


def record(conn,workspace,reservation_id,rate,model_id,usage):
    """Trusted worker only; caller owns the transaction, never expose as an API.

    Settlement and immutable receipt commit together. A provider refusal is not
    a reason to skip this. On UsageUnknown keep/reconcile the existing hold in a
    separate committed transaction; this function never guesses missing counters.
    """
    from . import wallets
    receipt=settlement(rate,model_id,usage)
    # Serialize with every reservation operation; verifies current actor too.
    wallets.reservation(conn,workspace,reservation_id)
    reserved_model=conn.execute('SELECT model_id FROM saas.reservations WHERE tenant_id=%s AND reservation_id=%s',
        (workspace.tenant_id,reservation_id)).fetchone()[0]
    if reserved_model!=model_id:
        raise SaaSError('Usage does not match the reserved model.','UsageUnknown',409)
    values=(model_id,rate.version,rate.input_per_thousand,rate.output_per_thousand,
            receipt['inputTokens'],receipt['outputTokens'],receipt['creditUnits'])
    previous=conn.execute('''SELECT model_id,rate_version,input_per_thousand,output_per_thousand,
        input_tokens,output_tokens,credit_units FROM saas.usage_receipts
        WHERE tenant_id=%s AND reservation_id=%s''',(workspace.tenant_id,reservation_id)).fetchone()
    if previous and tuple(previous)!=values:
        raise SaaSError('Usage receipt differs from its previous settlement.','IdempotencyConflict',409)
    wallets.complete(conn,workspace,reservation_id,receipt['creditUnits'])
    if not previous:
        conn.execute('''INSERT INTO saas.usage_receipts(tenant_id,reservation_id,model_id,rate_version,
            input_per_thousand,output_per_thousand,input_tokens,output_tokens,credit_units)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)''',(workspace.tenant_id,reservation_id,*values))
    return receipt

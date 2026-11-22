"""Recorded/verified INR payments only. Never activates AI or changes wallets."""
import hashlib
import re
import uuid
from psycopg.errors import UniqueViolation
from .money import parse_rupees,format_rupees
from .platform import require_platform
from .permissions import SaaSError


def audit(conn,tenant,actor,payment_id,action,request_id):
    conn.execute("SELECT set_config('radiology.tenant_id',%s,true)",(tenant,))
    conn.execute('''INSERT INTO saas.audit_logs(event_id,tenant_id,actor_id,action,object_id,request_id,outcome)
        VALUES(%s,%s,%s,%s,%s,%s,'SUCCESS')''',(uuid.uuid4(),tenant,actor,action,str(payment_id),request_id))


def create(connect,identity,payload,request_id):
    amount=parse_rupees(payload.get('amount')); tenant=payload.get('tenantId'); method=payload.get('method')
    reference=payload.get('reference'); reason=payload.get('reason')
    if not isinstance(tenant,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',tenant) or method not in ('BANK','UPI','CASH','OTHER'):
        raise SaaSError('Choose a customer and payment method.','InvalidPayment',400)
    if not isinstance(reference,str) or not re.fullmatch(r'[A-Za-z0-9 /_.:-]{1,160}',reference.strip()):
        raise SaaSError('Enter a valid transaction or cash receipt reference.','InvalidReference',400)
    if not isinstance(reason,str) or not 1<=len(reason.strip())<=1000: raise SaaSError('Give a reason for this record.','ReasonRequired',400)
    payment=uuid.uuid4(); reference=reference.strip()
    try:
        with connect() as conn:
            actor=require_platform(conn,identity)
            if not conn.execute('SELECT 1 FROM saas.tenants WHERE tenant_id=%s',(tenant,)).fetchone(): raise SaaSError('Customer not found.','NotFound',404)
            conn.execute('''INSERT INTO saas.manual_payments(payment_id,tenant_id,amount_paise,method,reference,reference_hash,reason,created_by)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',
                (payment,tenant,amount,method,reference,hashlib.sha256((method+':'+reference.casefold()).encode()).hexdigest(),reason.strip(),actor))
            audit(conn,tenant,actor,payment,'payment.recorded',request_id)
    except UniqueViolation: raise SaaSError('This payment reference has already been recorded.','DuplicatePayment',409) from None
    return {'paymentId':str(payment),'state':'RECORDED','version':1}


def decide(connect,identity,payment_id,payload,request_id):
    try: payment_id=uuid.UUID(payment_id)
    except (ValueError,TypeError): raise SaaSError('Payment not found.','NotFound',404) from None
    state=payload.get('state'); reason=payload.get('reason'); version=payload.get('version')
    if state not in ('VERIFIED','REJECTED') or type(version) is not int: raise SaaSError('Choose a valid payment decision.','InvalidPayment',400)
    if not isinstance(reason,str) or not 1<=len(reason.strip())<=1000: raise SaaSError('Give a verification/rejection reason.','ReasonRequired',400)
    with connect() as conn:
        actor=require_platform(conn,identity)
        row=conn.execute('SELECT tenant_id,amount_paise,state,version FROM saas.manual_payments WHERE payment_id=%s FOR UPDATE',(payment_id,)).fetchone()
        if not row: raise SaaSError('Payment not found.','NotFound',404)
        if row[2]!='RECORDED' or row[3]!=version: raise SaaSError('Payment was already decided or changed. Refresh the list.','PaymentChanged',409)
        if state=='VERIFIED' and parse_rupees(payload.get('receivedAmount'))!=row[1]:
            raise SaaSError('Verified received amount must match the payment record.','AmountMismatch',400)
        conn.execute('''UPDATE saas.manual_payments SET state=%s,version=version+1,verified_by=%s,
            decided_at=clock_timestamp(),decision_reason=%s WHERE payment_id=%s''',(state,actor,reason.strip(),payment_id))
        audit(conn,row[0],actor,payment_id,'payment.'+state.lower(),request_id)
    return {'paymentId':str(payment_id),'state':state,'version':version+1}


def listing(connect,identity):
    with connect() as conn:
        require_platform(conn,identity)
        rows=conn.execute('''SELECT payment_id,tenant_id,amount_paise,method,reference,state,version,created_at
            FROM saas.manual_payments ORDER BY created_at DESC,payment_id DESC LIMIT 100''').fetchall()
        return {'payments':[{'paymentId':str(r[0]),'tenantId':r[1],'amount':format_rupees(r[2]),'currency':'INR',
            'method':r[3],'reference':r[4],'state':r[5],'version':r[6],'createdAt':r[7].isoformat()} for r in rows]}

"""Integer credits with atomic reservations, expiring lots and immutable ledger.

These are customer accounting units, NOT money or exact model tokens. The
generation gateway must separately enforce subscription/model/usage pricing.
No network calls belong inside these short transactions.
"""
import uuid
from datetime import datetime
from .permissions import SaaSError

MAX_UNITS=10**15


def units(value,zero=False):
    if type(value) is not int or not (0 if zero else 1)<=value<=MAX_UNITS:
        raise SaaSError('Invalid credit amount.','InvalidUnits',400)
    return value


def lock(conn,tenant):
    conn.execute('INSERT INTO saas.wallets(tenant_id) VALUES(%s) ON CONFLICT DO NOTHING',(tenant,))
    conn.execute('SELECT tenant_id FROM saas.wallets WHERE tenant_id=%s FOR UPDATE',(tenant,))


def entry(conn,tenant,lot,kind,delta,reservation=None):
    if delta:
        conn.execute('INSERT INTO saas.wallet_ledger(event_id,tenant_id,lot_id,reservation_id,kind,delta_units) VALUES(%s,%s,%s,%s,%s,%s)',
                     (uuid.uuid4(),tenant,lot,reservation,kind,delta))


def expire(conn,tenant):
    # Caller holds wallet row; active holds survive expiry until settlement.
    rows=conn.execute('''SELECT lot_id,remaining_units-reserved_units FROM saas.credit_lots
        WHERE tenant_id=%s AND expires_at<=clock_timestamp() AND remaining_units>reserved_units FOR UPDATE''',(tenant,)).fetchall()
    for lot,amount in rows:
        conn.execute('UPDATE saas.credit_lots SET remaining_units=reserved_units WHERE tenant_id=%s AND lot_id=%s',(tenant,lot))
        entry(conn,tenant,lot,'EXPIRE',-amount)


def grant(conn,tenant,grant_key,amount,expires_at=None):
    """Trusted billing/operator operation; never expose directly to hospital users."""
    units(amount)
    if expires_at is not None and (not isinstance(expires_at,datetime) or expires_at.tzinfo is None):
        raise SaaSError('Credit expiry must be a timezone-aware timestamp.','InvalidExpiry',400)
    if not isinstance(grant_key,str) or not 1<=len(grant_key)<=160: raise SaaSError('Invalid grant identifier.','InvalidGrant',400)
    lock(conn,tenant)
    existing=conn.execute('SELECT lot_id,grant_units,expires_at FROM saas.credit_lots WHERE tenant_id=%s AND grant_key=%s',(tenant,grant_key)).fetchone()
    if existing:
        if existing[1]!=amount or existing[2]!=expires_at: raise SaaSError('Credit grant differs from its previous use.','IdempotencyConflict',409)
        return str(existing[0])
    lot=uuid.uuid4()
    conn.execute('''INSERT INTO saas.credit_lots(lot_id,tenant_id,grant_key,grant_units,remaining_units,expires_at)
        VALUES(%s,%s,%s,%s,%s,%s)''',(lot,tenant,grant_key,amount,amount,expires_at))
    entry(conn,tenant,lot,'GRANT',amount)
    return str(lot)


def balance(conn,tenant):
    row=conn.execute('''SELECT coalesce(sum(CASE WHEN expires_at IS NULL OR expires_at>clock_timestamp()
        THEN remaining_units-reserved_units ELSE 0 END),0),coalesce(sum(reserved_units),0)
        FROM saas.credit_lots WHERE tenant_id=%s''',(tenant,)).fetchone()
    return {'availableUnits':int(row[0]),'reservedUnits':int(row[1])}


def reserve(conn,workspace,request_id,model_id,amount):
    workspace.require('ai.generate'); units(amount)
    try: request_id=uuid.UUID(str(request_id))
    except (ValueError,TypeError,AttributeError): raise SaaSError('Invalid request identifier.','InvalidRequest',400) from None
    if not isinstance(model_id,str) or not 1<=len(model_id)<=512: raise SaaSError('Invalid model.','InvalidModel',400)
    tenant=workspace.tenant_id; lock(conn,tenant); expire(conn,tenant)
    previous=conn.execute('SELECT reservation_id,actor_id,model_id,hold_units,state FROM saas.reservations WHERE tenant_id=%s AND request_id=%s',(tenant,request_id)).fetchone()
    if previous:
        if str(previous[1])!=workspace.user_id or previous[2]!=model_id or previous[3]!=amount:
            raise SaaSError('Request identifier was already used with different parameters.','IdempotencyConflict',409)
        return {'reservationId':str(previous[0]),'state':previous[4],'created':False}
    lots=conn.execute('''SELECT lot_id,remaining_units-reserved_units FROM saas.credit_lots
        WHERE tenant_id=%s AND (expires_at IS NULL OR expires_at>clock_timestamp())
          AND remaining_units>reserved_units ORDER BY expires_at NULLS LAST,created_at,lot_id FOR UPDATE''',(tenant,)).fetchall()
    if sum(row[1] for row in lots)<amount: raise SaaSError('Insufficient available AI credits.','InsufficientCredits',402)
    reservation=uuid.uuid4()
    conn.execute('''INSERT INTO saas.reservations(reservation_id,tenant_id,request_id,actor_id,model_id,hold_units)
        VALUES(%s,%s,%s,%s,%s,%s)''',(reservation,tenant,request_id,workspace.user_id,model_id,amount))
    remaining=amount
    for lot,available in lots:
        held=min(remaining,available)
        if not held: break
        conn.execute('UPDATE saas.credit_lots SET reserved_units=reserved_units+%s WHERE tenant_id=%s AND lot_id=%s',(held,tenant,lot))
        conn.execute('INSERT INTO saas.reservation_lots(tenant_id,reservation_id,lot_id,units) VALUES(%s,%s,%s,%s)',(tenant,reservation,lot,held))
        remaining-=held
    return {'reservationId':str(reservation),'state':'RESERVED','created':True}


def reservation(conn,workspace,reservation_id):
    lock(conn,workspace.tenant_id)
    row=conn.execute('SELECT state,hold_units,actual_units,actor_id FROM saas.reservations WHERE tenant_id=%s AND reservation_id=%s FOR UPDATE',
                     (workspace.tenant_id,reservation_id)).fetchone()
    if not row or str(row[3])!=workspace.user_id: raise SaaSError('Reservation not found.','NotFound',404)
    return row


def begin(conn,workspace,reservation_id):
    row=reservation(conn,workspace,reservation_id)
    if row[0]!='RESERVED': raise SaaSError('This request has already started or finished.','InvocationAlreadyStarted',409)
    conn.execute("UPDATE saas.reservations SET state='INVOKING',started_at=clock_timestamp() WHERE tenant_id=%s AND reservation_id=%s",(workspace.tenant_id,reservation_id))


def unknown(conn,workspace,reservation_id):
    row=reservation(conn,workspace,reservation_id)
    if row[0]=='OUTCOME_UNKNOWN': return
    if row[0]!='INVOKING': raise SaaSError('Request cannot enter unknown state.','InvalidReservationState',409)
    conn.execute("UPDATE saas.reservations SET state='OUTCOME_UNKNOWN' WHERE tenant_id=%s AND reservation_id=%s",(workspace.tenant_id,reservation_id))


def complete(conn,workspace,reservation_id,actual=None):
    """None releases ONLY a never-invoked hold; an unknown invocation stays held."""
    row=reservation(conn,workspace,reservation_id)
    if actual is None:
        if row[0]=='RELEASED': return {'state':'RELEASED'}
        if row[0]!='RESERVED': raise SaaSError('Invoked requests need reconciled usage; their hold cannot be released automatically.','OutcomeUnknown',409)
        debit=0; state='RELEASED'
    else:
        debit=units(actual,zero=True); state='SETTLED'
        if row[0]=='SETTLED':
            if row[2]!=debit: raise SaaSError('Usage differs from recorded settlement.','IdempotencyConflict',409)
            return {'state':'SETTLED'}
        if row[0] not in ('INVOKING','OUTCOME_UNKNOWN'): raise SaaSError('Request was not invoked.','InvalidReservationState',409)
        if debit>row[1]: raise SaaSError('Usage exceeds its reservation. Reconciliation is required.','ReservationExceeded',409)
    remaining=debit; tenant=workspace.tenant_id
    lots=conn.execute('''SELECT a.lot_id,a.units FROM saas.reservation_lots a JOIN saas.credit_lots l USING(tenant_id,lot_id)
        WHERE a.tenant_id=%s AND a.reservation_id=%s ORDER BY l.expires_at NULLS LAST,l.created_at,l.lot_id''',(tenant,reservation_id)).fetchall()
    for lot,held in lots:
        charged=min(remaining,held)
        conn.execute('UPDATE saas.credit_lots SET reserved_units=reserved_units-%s,remaining_units=remaining_units-%s WHERE tenant_id=%s AND lot_id=%s',
                     (held,charged,tenant,lot))
        entry(conn,tenant,lot,'DEBIT',-charged,reservation_id); remaining-=charged
    conn.execute('UPDATE saas.reservations SET state=%s,actual_units=%s,completed_at=clock_timestamp() WHERE tenant_id=%s AND reservation_id=%s',
                 (state,debit if state=='SETTLED' else None,tenant,reservation_id))
    expire(conn,tenant)
    return {'state':state}

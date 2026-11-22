"""Patient conversations persisted independently from application snapshots."""
from contextlib import contextmanager
from contextvars import copy_context
from datetime import datetime, timedelta, timezone
import logging
import hashlib
import re
import threading
import uuid

RETENTION_DAYS = 14
HISTORY_MESSAGES = 5
HISTORY_CHARS = 12000
REPORT_CHARS = 30000
PROMPT_CHARS = 6000
JOB_MINUTES = 10
LOG = logging.getLogger('radiology.chat')


def editor_hash(text):
    return hashlib.sha256(text.strip().encode()).hexdigest()


def resolve_turn(payload, messages):
    """Select conversation baseline while preserving unsent manual editor changes."""
    from ingestion.documents import classify, normalize_modality
    prompt = str(payload.get('prompt') or '').strip()
    current = str(payload.get('currentReport') or '').strip()
    detected = classify(prompt)
    selected = normalize_modality(payload.get('modality'))
    explicit_modality = detected['modality']
    modality = payload.get('_resolvedModality') or (explicit_modality if explicit_modality != 'UNKNOWN' else selected)
    latest = next((m for m in reversed(messages) if m.get('draft') and not m.get('error')
                   and m.get('reportId') == payload.get('reportId')), None)
    baseline = current
    unchanged = latest and (not current or latest.get('editorHash') == editor_hash(current))
    if unchanged and ((explicit_modality == 'UNKNOWN' and not payload.get('_resolvedModality')) or normalize_modality(latest.get('modality')) == modality):
        baseline = latest['draft']
        if explicit_modality == 'UNKNOWN' and not payload.get('_resolvedModality'):
            modality = normalize_modality(latest.get('modality'))
    requested = payload.get('intent', 'auto')
    if requested not in ('new', 'edit', 'auto'):
        raise ValueError('Invalid conversation action.')
    new_request = bool(re.search(r'\b(?:new|another|fresh)\s+(?:\w+\s+){0,4}report\b|\b(?:draft|generate|create|write|give)\b.{0,60}\breport\b', prompt, re.I))
    previous = classify(baseline) if baseline else {'modality':'UNKNOWN','body_region':'UNKNOWN'}
    different_study = (explicit_modality != 'UNKNOWN' and previous['modality'] != 'UNKNOWN'
                       and explicit_modality != previous['modality']) or (
        detected['body_region'] != 'UNKNOWN' and previous['body_region'] != 'UNKNOWN'
        and detected['body_region'] != previous['body_region'])
    mode = requested if requested != 'auto' else ('new' if new_request or different_study or not baseline else 'edit')
    return mode, baseline if mode == 'edit' else current, modality


def utcnow():
    return datetime.now(timezone.utc)


def fresh(now):
    return {'messages': [], 'pending': None, 'expiresAt': (now + timedelta(days=RETENTION_DAYS)).isoformat()}


def bounded_history(messages):
    """Most recent five successful messages, oldest first, with a shared size cap."""
    selected = [m for m in messages if not m.get('error')][-HISTORY_MESSAGES:]
    result, remaining = [], HISTORY_CHARS
    for message in reversed(selected):
        if remaining <= 0:
            break
        content = message.get('draft') or message['text']
        content = content[:remaining]
        remaining -= len(content)
        result.append({'role': message['role'], 'content': content,
                       'reportId': message.get('reportId'), 'modality': message.get('modality')})
    return list(reversed(result))


class ChatConflict(ValueError):
    pass


class PostgresChatStore:
    def __init__(self, connect, initialize=True):
        self.connect = connect
        if not initialize: return
        with connect() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS patient_conversations (
                tenant_id text NOT NULL, patient_id text NOT NULL,
                payload jsonb NOT NULL, expires_at timestamptz NOT NULL,
                PRIMARY KEY (tenant_id, patient_id)
            )''')
            conn.execute('CREATE INDEX IF NOT EXISTS patient_conversations_expiry ON patient_conversations(expires_at)')

    @contextmanager
    def transaction(self, tenant, patient, now):
        from psycopg.types.json import Jsonb
        with self.connect() as conn:
            initial = fresh(now)
            conn.execute('''INSERT INTO patient_conversations(tenant_id,patient_id,payload,expires_at)
                VALUES(%s,%s,%s,%s) ON CONFLICT (tenant_id,patient_id)
                DO UPDATE SET patient_id=EXCLUDED.patient_id''',
                (tenant, patient, Jsonb(initial), initial['expiresAt']))
            row = conn.execute('''SELECT payload,expires_at FROM patient_conversations
                WHERE tenant_id=%s AND patient_id=%s FOR UPDATE''', (tenant, patient)).fetchone()
            value = row[0] if row[1] > now else initial
            yield value
            conn.execute('''UPDATE patient_conversations SET payload=%s,expires_at=%s
                WHERE tenant_id=%s AND patient_id=%s''',
                (Jsonb(value), value['expiresAt'], tenant, patient))

    def purge(self, now):
        with self.connect() as conn:
            conn.execute('DELETE FROM patient_conversations WHERE expires_at<=%s', (now,))


class PatientChat:
    def __init__(self, store, generate, clock=utcnow, dispatch=None):
        self.store, self.generate, self.clock = store, generate, clock
        self.dispatch = dispatch or (lambda target: threading.Thread(target=copy_context().run, args=(target,), daemon=True).start())

    def _expire_job(self, state, now):
        pending = state.get('pending')
        if pending and datetime.fromisoformat(pending['startedAt']) + timedelta(minutes=JOB_MINUTES) <= now:
            state['messages'].append({'id': uuid.uuid4().hex, 'role': 'assistant', 'error': True,
                'text': 'Generation was interrupted or timed out. Send your request again.',
                'createdAt': now.isoformat(), 'reportId': pending.get('reportId'),
                'modality': pending.get('modality'), 'requestId': pending['id']})
            state['pending'] = None
            state['expiresAt'] = (now + timedelta(days=RETENTION_DAYS)).isoformat()

    def read(self, tenant, patient):
        now = self.clock()
        with self.store.transaction(tenant, patient, now) as state:
            self._expire_job(state, now)
            return {**state, 'retentionDays': RETENTION_DAYS, 'contextMessages': HISTORY_MESSAGES}

    def clear(self, tenant, patient):
        now = self.clock()
        with self.store.transaction(tenant, patient, now) as state:
            state.clear()
            state.update(fresh(now))
            # Any previously running job no longer matches a pending request.
            return {**state, 'retentionDays': RETENTION_DAYS, 'contextMessages': HISTORY_MESSAGES}

    def submit(self, tenant, patient, payload):
        instruction = str(payload.get('prompt') or '').strip()
        current = str(payload.get('currentReport') or '').strip()
        requested = payload.get('intent', 'auto')
        if not instruction or len(instruction) > PROMPT_CHARS:
            raise ValueError('Enter a request of 1 to 6000 characters.')
        if len(current) > REPORT_CHARS:
            raise ValueError('The current report exceeds the 30000-character editing limit.')
        if requested not in ('new', 'edit', 'auto'):
            raise ValueError('Invalid conversation action.')
        if requested == 'edit' and not current:
            raise ValueError('Insert or open a report before requesting changes to it.')
        request_id = str(payload.get('requestId') or uuid.uuid4())
        try:
            uuid.UUID(request_id)
        except ValueError:
            raise ValueError('Invalid request ID.')
        now = self.clock()
        with self.store.transaction(tenant, patient, now) as state:
            self._expire_job(state, now)
            if any(m.get('requestId') == request_id for m in state['messages']):
                return {'requestId': request_id, 'status': 'ACCEPTED'}
            if state.get('pending'):
                raise ChatConflict('A report is already being generated for this patient.')
            mode, baseline, modality = resolve_turn(payload, state['messages'])
            history = bounded_history(state['messages'])
            pending = {'id': request_id, 'startedAt': now.isoformat(),
                       'reportId': payload.get('reportId'), 'modality': modality,
                       'editorHash': editor_hash(current)}
            state['messages'].append({'id': uuid.uuid4().hex, 'role': 'user', 'text': instruction,
                'createdAt': now.isoformat(), 'requestId': request_id, 'intent': mode,
                'reportId': pending['reportId'], 'modality': pending['modality']})
            state['pending'] = pending
            state['expiresAt'] = (now + timedelta(days=RETENTION_DAYS)).isoformat()
        # Construct model input server-side; do not accept client-supplied history.
        generation = {key: payload[key] for key in ('modality','bodyPart','clinicalHistory','studyName','confirmedModality','_requestId') if key in payload}
        generation.update(patientId=patient, prompt=instruction, currentReport=baseline,
                          modality=modality, intent=mode, _chatHistory=history)
        def work():
            try:
                result = self.generate(generation)
            except Exception:
                LOG.error('Chat generation failed request=%s', request_id)
                result = {'mode': 'error', 'warning': 'Generation failed. Please retry.'}
            try:
                self.finish(tenant, patient, request_id, result)
            except Exception:
                LOG.error('Chat result persistence failed request=%s', request_id)
        try:
            self.dispatch(work)
        except Exception:
            self.finish(tenant, patient, request_id, {'mode':'error','warning':'Could not start generation. Please retry.'})
        return {'requestId': request_id, 'status': 'ACCEPTED'}

    def finish(self, tenant, patient, request_id, result):
        now = self.clock()
        with self.store.transaction(tenant, patient, now) as state:
            pending = state.get('pending')
            if not pending or pending['id'] != request_id:
                return  # Cleared/expired conversation or superseded job.
            valid = bool(result.get('text')) and result.get('mode') in ('aws', 'openai')
            message = {'id': uuid.uuid4().hex, 'role': 'assistant', 'createdAt': now.isoformat(),
                'requestId': request_id, 'reportId': pending.get('reportId'), 'modality': pending.get('modality'),
                'error': not valid, 'text': result.get('warning') or ('Draft ready. Review before inserting.' if valid else 'No draft was generated. Please retry.')}
            if valid:
                message.update(draft=result['text'], citations=result.get('citations', []), editorHash=pending.get('editorHash'))
            state['messages'].append(message)
            state['pending'] = None
            state['expiresAt'] = (now + timedelta(days=RETENTION_DAYS)).isoformat()

    def start_cleanup(self):
        def clean():
            while True:
                try:
                    self.store.purge(self.clock())
                except Exception:
                    LOG.error('Chat retention cleanup failed; will retry.')
                threading.Event().wait(3600)
        threading.Thread(target=clean, daemon=True).start()

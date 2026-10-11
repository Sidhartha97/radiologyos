# Patient chat

Conversations are stored in PostgreSQL `patient_conversations`, keyed by the
authenticated hospital/tenant and patient ID. All reports for that patient
share one chat history. Switching patients changes the history shown. Closing
the panel only hides it; reopening or refreshing loads the stored conversation.

## Use

1. Rebuild with `docker compose up -d --build radiologyos`.
2. Check `/api/health` reports PostgreSQL connected. The conversation table and
   expiry index are created automatically at startup or first chat request.
3. Open a patient report, open chat, and describe the report you want. There is
   no New/Modify dropdown; an explicit new report/study request starts a new draft.
4. Ask follow-up questions or changes directly. The previous draft is used even
   before you apply it. If you manually change the editor, those changes take priority.
5. The model receives the editing baseline and at most five prior non-error
   messages. Review and apply the
   returned draft. **Undo AI replacement** restores the editor content from
   immediately before the latest application (until leaving/reloading the report).
6. Saving/finalizing the report remains an explicit editor action. Patient chat
   does not need an automatically saved report to obtain a stable patient ID.

Historical messages from other reports for the same patient remain visible with
their modality and timestamp. Any successful draft in this patient's chat can
be inserted into the currently open editor, including drafts from another report
or modality. Insertion replaces the editor content and selects the draft's modality
when available in the modality catalogue. It does not save or finalize anything.
Undo AI replacement restores both the previous content and modality.

## Retention and context

- Conversation expires 14 days after its last stored message. Viewing it does
  not extend retention. A new message extends the conversation's expiry.
- Expired history is excluded immediately when read. A running server also
  purges expired rows at startup and hourly. If stopped, purge resumes on start.
- Chat expiry/clearing does not delete saved reports or report versions. Existing
  database backup retention is independent of live-row cleanup.
- LLM context: up to five prior non-error messages, with a combined 12,000-character
  limit, plus the current report (30,000-character maximum) and new request
  (6,000-character maximum). Oversized current reports are rejected rather than
  silently truncated. Older messages remain visible until conversation expiry.
- New-draft mode keeps prior study findings out of the new examination. Edit mode
  preserves manual changes and bypasses new historical retrieval.
- Conversation text is not automatically published to S3 or indexed for RAG.

## Requests and recovery

`GET /api/patient-chat/{patientId}` returns messages, expiry, and pending status.
`POST /api/rag/chat` accepts a request and returns HTTP 202. A server worker stores
the result independently of the browser connection. The panel polls the patient
conversation while work is pending. Repeated request IDs do not duplicate work;
only one generation per patient is accepted at a time.

`DELETE /api/patient-chat/{patientId}` clears that conversation after UI confirmation.
Late worker results cannot repopulate a cleared conversation. Interrupted jobs
are marked failed after ten minutes when the conversation is checked; a retry is
then allowed. In-flight work is not automatically resumed after server restart.

PostgreSQL errors are displayed in the panel; chat does not fall back to volatile
memory or JSON. Previously lost browser-only messages cannot be reconstructed.
An unavailable history does not permanently disable Send: requests retry the
PostgreSQL connection, and the panel automatically retries loading. If storage
is still unavailable, generation is rejected with an error rather than losing history.

The application now requires a verified Cognito access token and a backend
hospital membership. These routes validate patient/report ownership within that
resolved hospital. See SECURE_HOSPITAL_SETUP.md for account provisioning,
legacy-data migration and remaining deployment requirements.

## Verification

Automated service tests cover patient/tenant separation, reopening, retention,
context bounds, duplicate requests, clearing during generation, failures and
restart timeout. Generation tests check that edit mode supplies current editor
content and conversation history without replacing it through historical retrieval.

Manual deployment check: send a message for patient A, close/reopen chat, refresh,
then open patient B and confirm a separate history. Return to A, apply its draft,
manually change a sentence, and request a different edit. Verify the manual sentence
is retained. Clear A's history and confirm B's history and saved reports remain.

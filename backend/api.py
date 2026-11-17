"""FastAPI integration for scoped, durable multimodal RAG.

The files explicitly protected by the project owner are not modified. The API
uses the integration workflow in orchestration.py; the legacy graph is retained.
"""
from __future__ import annotations
import hashlib
import hmac
import logging
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import Any
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from backend import runtime_settings as settings
from backend.repository import Repository
from backend.file_processing import extract_file, FileValidationError, validate_image
from backend.orchestration import Orchestrator, document_part, harmful, metadata_answer, intent_for, REFUSAL, SYSTEM
from backend.integrations import ServiceUnavailable
from backend.security.prompt_guard import check_prompt
from backend.security.output_guard import sanitize_output
from backend.evaluation import EvaluationTracker, EvaluationResult
from backend.observability import RunObserver, load_observability_history, calculate_observability_summary, clear_observability_history

logger = logging.getLogger(__name__)
repository = Repository()
rag_service = Orchestrator(repository)
api = FastAPI(title='Agentic Adaptive RAG API', version='2.0.0')
app = api
api.add_middleware(CORSMiddleware, allow_origins=settings.ALLOWED_ORIGINS,
                   allow_credentials=True, allow_methods=['GET', 'POST', 'DELETE', 'OPTIONS'],
                   allow_headers=['Content-Type', 'X-Legacy-Import-Key'])


@api.middleware('http')
async def scoped_session(request: Request, call_next):
    from starlette.responses import JSONResponse
    origin = request.headers.get('origin')
    if request.method not in {'GET', 'HEAD', 'OPTIONS'} and origin and origin not in settings.ALLOWED_ORIGINS:
        return JSONResponse({'detail': 'This origin is not authorized.'}, status_code=403)
    cookie = request.cookies.get(settings.COOKIE_NAME, '')
    scope, _, signature = cookie.partition('.')
    valid = (len(scope) == 64 and all(c in '0123456789abcdef' for c in scope) and
             hmac.compare_digest(signature, hmac.new(repository.secret(), scope.encode(), hashlib.sha256).hexdigest()))
    if not valid:
        scope = secrets.token_hex(32)
    request.state.scope = scope
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    if not valid:
        signature = hmac.new(repository.secret(), scope.encode(), hashlib.sha256).hexdigest()
        response.set_cookie(settings.COOKIE_NAME, scope + '.' + signature, httponly=True,
                            secure=settings.COOKIE_SECURE, samesite=settings.COOKIE_SAMESITE,
                            max_age=365 * 86400)
    return response


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = None

class HITLRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=128)
    approved: bool


def logs(scope):
    root = repository.root / 'sessions' / scope
    root.mkdir(parents=True, exist_ok=True)
    return root


def record(question, result, latency, scope, error=''):
    try:
        root = logs(scope)
    except OSError:
        logger.exception('Could not open scoped metrics directory')
        return
    fields = {k: result.get(k, default) for k, default in {
        'route': 'unknown', 'retrieved_documents': 0, 'relevant_documents': 0,
        'grounded': None, 'answers_question': None, 'retry_count': 0,
        'sources': [], 'hitl_status': '', 'hitl_reason': '',
    }.items()}
    # Observability includes approval pauses and final attempts. Keep it separate
    # from completed/failed evaluation records, and isolate write failures.
    try:
        RunObserver(question, storage_path=root / 'observability.jsonl').record(
            **fields, latency_seconds=latency, success=not bool(error) and result.get('success') is not False and result.get('status') != 'failed', error=error)
    except Exception:
        logger.exception('Could not record scoped observability metrics')
    if result.get('status') == 'approval_required' or result.get('hitl_status') == 'pending':
        return
    try:
        fields.pop('hitl_status')
        fields.pop('hitl_reason')
        # These routes do not run the RAG grounding/answer graders. Their
        # execution can succeed without implying a passed quality assessment.
        if result.get('route') in {'direct', 'image', 'summary', 'security_blocked'}:
            fields['grounded'] = None
            fields['answers_question'] = None
        EvaluationTracker(root / 'evaluation.jsonl').add(EvaluationResult(
            question=question, answer=result.get('answer', ''), **fields,
            latency_seconds=latency, error=error, request_id=result.get('thread_id', ''),
            status='failed' if error else result.get('status', 'completed'),
            execution_success=result.get('success')))
    except Exception:
        logger.exception('Could not record scoped evaluation metrics')


def payload(result, thread_id, latency=0):
    data = {k: v for k, v in result.items() if k not in {'__interrupt__', 'scope', 'documents', 'blocked'}}
    interrupts = result.get('__interrupt__') or []
    data.update(status='approval_required' if interrupts else 'completed', thread_id=thread_id,
                generation=result.get('answer', ''), latency_seconds=round(latency, 4))
    if interrupts:
        data.update(hitl_status='pending', hitl_reason=interrupts[0].value['message'], hitl=interrupts[0].value)
    return data


def owned_document(scope, identifier):
    row = repository.get(scope, identifier)
    if row is None:
        raise HTTPException(404, 'Document not found in this session.')
    return row


@api.get('/')
def root():
    return {'name': 'Agentic Adaptive RAG API', 'status': 'running'}

@api.get('/health')
def health():
    # Liveness is independent of external credentials; report their presence, never values.
    return {'status': 'healthy', 'evaluation_enabled': True, 'observability_enabled': True,
            'configured_services': {name: bool(os.getenv(name)) for name in ('GROQ_API_KEY', 'GOOGLE_API_KEY', 'TAVILY_API_KEY')}}

@api.get('/config')
def configuration():
    return {'upload_limits': {ext: {'max_bytes': limit, 'max_mb': limit / settings.MIB} for ext, limit in settings.UPLOAD_LIMITS.items()},
            'max_files': settings.MAX_FILES, 'image_max_bytes': settings.IMAGE_LIMIT,
            'web_search_requires_approval': settings.WEB_APPROVAL,
            'legacy_import_available': bool(os.getenv('LEGACY_IMPORT_KEY')) and Path(os.getenv('CHROMA_PATH', './.chroma'), 'chroma.sqlite3').is_file()}

@api.get('/documents')
def get_documents(request: Request):
    documents = repository.list(request.state.scope)
    return {'documents': documents, 'count': len(documents)}

@api.post('/documents/import-legacy')
def import_legacy(request: Request):
    expected = os.getenv('LEGACY_IMPORT_KEY', '')
    supplied = request.headers.get('X-Legacy-Import-Key', '')
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        raise HTTPException(403, 'Configure LEGACY_IMPORT_KEY and supply the administrator import key.')
    from backend.legacy_recovery import recover_legacy
    source = Path(os.getenv('CHROMA_PATH', './.chroma')) / 'chroma.sqlite3'
    if not source.is_file():
        raise HTTPException(404, 'No legacy database was found.')
    try:
        files = recover_legacy(repository, request.state.scope, source)
        return {'message': f'Recovered metadata for {len(files)} document(s). Reupload original files for exact text and complete summaries, then retry indexing.',
                'recovered': files, 'documents': repository.list(request.state.scope)}
    except Exception as exc:
        logger.exception('Legacy recovery failed')
        raise HTTPException(422, 'The legacy database could not be read; reupload your original documents.') from exc

@api.get('/documents/{document_id}/text')
def get_document_text(document_id: str, request: Request, part: int = Query(1, ge=1)):
    row = owned_document(request.state.scope, document_id)
    if not row['extraction_complete']:
        raise HTTPException(409, 'Original text is unavailable for this legacy file. Reupload the original for exact text.')
    try:
        return document_part(row, part)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

@api.post('/documents/{document_id}/index')
def retry_index(document_id: str, request: Request):
    row = owned_document(request.state.scope, document_id)
    try:
        count = rag_service.index.add(row)
        repository.mark_indexed(request.state.scope, document_id, count)
        return {'message': 'Document indexed.', 'document_id': document_id, 'chunk_count': count}
    except ServiceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc

@api.delete('/documents/{document_id}')
def remove_document(document_id: str, request: Request):
    owned_document(request.state.scope, document_id)
    try:
        rag_service.index.delete(request.state.scope, document_id)
        repository.delete(request.state.scope, document_id)
        return {'message': 'Document deleted.', 'document_id': document_id}
    except Exception as exc:
        logger.exception('Deletion failed')
        raise HTTPException(503, 'Could not delete the document index. Please retry.') from exc

@api.post('/upload')
async def upload_documents(request: Request, files: list[UploadFile] = File(...)):
    uploaded, rejected, statuses = [], [], []
    try:
        if len(files) > settings.MAX_FILES:
            raise HTTPException(400, f'Upload at most {settings.MAX_FILES} files at once.')
        for file in files:
            filename = (file.filename or '').replace('\\', '/').rsplit('/', 1)[-1]
            ext = Path(filename).suffix.lower()
            try:
                if not filename or len(filename) > 200 or any(ord(c) < 32 for c in filename):
                    raise HTTPException(400, 'Invalid filename.')
                if ext not in settings.UPLOAD_LIMITS:
                    raise HTTPException(415, 'Unsupported file type. Supported: PDF, DOCX, TXT, MD.')
                limit = settings.UPLOAD_LIMITS[ext]
                if file.size is not None and file.size > limit:
                    raise HTTPException(413, size_error(ext, file.size, limit))
                data = await file.read(limit + 1)
                if len(data) > limit:
                    raise HTTPException(413, size_error(ext, file.size or len(data), limit))
                extracted = await run_in_threadpool(extract_file, filename, data, file.content_type)
                doc, duplicate = await run_in_threadpool(repository.put, request.state.scope, filename, data, extracted)
                if doc['status'] != 'ready':
                    row = repository.get(request.state.scope, doc['document_id'])
                    try:
                        count = await run_in_threadpool(rag_service.index.add, row)
                        repository.mark_indexed(request.state.scope, doc['document_id'], count)
                        doc['status'], doc['chunk_count'] = 'ready', count
                    except ServiceUnavailable as exc:
                        raise HTTPException(503, str(exc) + f" Document '{filename}' is saved and can be displayed in full.") from exc
                uploaded.append({**doc, 'duplicate': duplicate})
            except (HTTPException, FileValidationError, ValueError) as exc:
                status = exc.status_code if isinstance(exc, HTTPException) else (422 if isinstance(exc, FileValidationError) else 409)
                message = exc.detail if isinstance(exc, HTTPException) else str(exc)
                rejected.append({'file_name': filename, 'reason': message})
                statuses.append(status)
            except Exception:
                logger.exception('Unexpected upload failure for %r', filename)
                rejected.append({'file_name': filename, 'reason': 'Document processing failed. Check backend logs and retry.'})
                statuses.append(500)
        if not uploaded:
            raise HTTPException(statuses[0] if statuses else 400,
                                '; '.join(f"{r['file_name']}: {r['reason']}" for r in rejected) or 'No files were uploaded.')
        return {'message': f'Processed {len(uploaded)} file(s).', 'uploaded': uploaded, 'rejected': rejected,
                'documents': repository.list(request.state.scope)}
    finally:
        for file in files:
            await file.close()


def size_error(ext, size, limit):
    return (f'File upload failed. The maximum allowed {ext[1:].upper()} size is {limit/settings.MIB:g} MB. '
            f'Your uploaded file is {size/settings.MIB:.2f} MB. Please upload a smaller file.')

@api.post('/chat')
def chat(request: ChatRequest, http_request: Request):
    question = request.question.strip()
    if not question:
        raise HTTPException(400, 'Question cannot be empty.')
    # Each invocation gets fresh state; caller-supplied IDs cannot reuse another run.
    thread_id = str(uuid.uuid4())
    scope = http_request.state.scope
    start = time.perf_counter()
    try:
        result = rag_service.run(question, scope, thread_id)
        if result.get('__interrupt__'):
            repository.thread(scope, thread_id, time.time() + settings.APPROVAL_TTL)
        else:
            rag_service.forget(scope, thread_id)
        result = payload(result, thread_id, time.perf_counter() - start)
        record(question, result, time.perf_counter() - start, scope)
        return result
    except ServiceUnavailable as exc:
        record(question, {'thread_id': thread_id}, time.perf_counter() - start, scope, str(exc))
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        record(question, {'thread_id': thread_id}, time.perf_counter() - start, scope, str(exc))
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception('Chat execution failed')
        record(question, {'thread_id': thread_id}, time.perf_counter() - start, scope, 'Chat execution failed')
        raise HTTPException(503, 'The request could not be completed. Check service configuration and retry.') from exc

@api.post('/chat/approval')
def chat_approval(request: HITLRequest, http_request: Request):
    scope = http_request.state.scope
    expires = repository.thread_expiry(scope, request.thread_id)
    if expires is None or expires < time.time():
        raise HTTPException(409, 'Approval session is missing or expired. Ask the question again.')
    config = rag_service.config(scope, request.thread_id)
    snapshot = rag_service.graph.get_state(config)
    if not snapshot.tasks:
        raise HTTPException(409, 'Approval session ended or the server restarted. Ask the question again.')
    if not repository.consume_thread(scope, request.thread_id):
        raise HTTPException(409, 'This approval has already been handled.')
    start = time.perf_counter()
    try:
        result = rag_service.resume(scope, request.thread_id, request.approved)
        rag_service.forget(scope, request.thread_id)
        response = payload(result, request.thread_id, time.perf_counter() - start)
        record(result.get('question', ''), response, time.perf_counter() - start, scope)
        return response
    except ServiceUnavailable as exc:
        record(snapshot.values.get('question', ''), {'thread_id': request.thread_id}, time.perf_counter() - start, scope, str(exc))
        raise HTTPException(503, str(exc) + ' Ask the question again to retry.') from exc
    except Exception as exc:
        logger.exception('Approval resume failed')
        record(snapshot.values.get('question', ''), {'thread_id': request.thread_id}, time.perf_counter() - start, scope, 'Approval resume failed')
        raise HTTPException(503, 'Could not resume this request. Ask the question again.') from exc

@api.post('/chat/image')
async def chat_with_image(request: Request, question: str = Form(''), thread_id: str | None = Form(None), image: UploadFile = File(...)):
    from backend import image_search
    question = question.strip() or 'Explain the uploaded image and identify its important content.'
    active_thread = str(uuid.uuid4())
    started = time.perf_counter()
    checked = check_prompt(question)
    try:
        if not checked.allowed or harmful(question):
            response = payload({'question': question, 'answer': REFUSAL if harmful(question) else "I can't process instruction-override requests.",
                            'route': 'security_blocked', 'security_status': 'blocked', 'sources': []}, active_thread)
            record(question, response, time.perf_counter() - started, request.state.scope)
            return response
        data = await image.read(settings.IMAGE_LIMIT + 1)
        detected = await run_in_threadpool(validate_image, data, image.content_type)
        blocked = await run_in_threadpool(rag_service.check_tool_safety, {'question': question})
        if blocked:
            response = payload(blocked, active_thread)
            record(question, response, time.perf_counter() - started, request.state.scope)
            return response
        start = time.perf_counter()
        answer = await run_in_threadpool(image_search.answer_image_question, data, detected, question)
        guarded = sanitize_output(answer)
        related = []
        if any(p in question.lower() for p in ('find similar images', 'find related images', 'show similar images', 'search for images', 'find images')):
            # Explicit image-search request authorizes read-only search. The text web
            # approval configuration applies here too: direct users to the web route.
            if settings.WEB_APPROVAL:
                answer = guarded.output + '\n\nRelated image search requires a separate approved web-search request on this installation.'
            else:
                related = await run_in_threadpool(image_search.search_related_images, question + '\n' + guarded.output[:1500])
                answer = guarded.output
        else:
            answer = guarded.output
        result = payload({'question': question, 'answer': answer, 'route': 'image', 'sources': [],
                          'related_images': related, 'security_status': 'redacted' if guarded.redactions else 'passed',
                          'security_redactions': guarded.redactions, 'grounded': False, 'answers_question': True}, active_thread, time.perf_counter() - start)
        record(question, result, time.perf_counter() - start, request.state.scope)
        return result
    except FileValidationError as exc:
        raise HTTPException(413 if len(data) > settings.IMAGE_LIMIT else 422, str(exc)) from exc
    except ServiceUnavailable as exc:
        record(question, {'thread_id': active_thread, 'route': 'image'}, time.perf_counter() - started, request.state.scope, str(exc))
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        logger.exception('Image analysis failed')
        record(question, {'thread_id': active_thread, 'route': 'image'}, time.perf_counter() - started, request.state.scope, 'Image analysis failed')
        raise HTTPException(503, 'Image analysis is unavailable. Check the configured vision model and Google API key.') from exc
    finally:
        await image.close()

@api.get('/evaluation')
def get_evaluation(request: Request):
    tracker = EvaluationTracker(logs(request.state.scope) / 'evaluation.jsonl')
    return {'results': [r.to_dict() for r in tracker.results], 'summary': tracker.summary()}

@api.delete('/evaluation')
def clear_evaluation(request: Request):
    EvaluationTracker(logs(request.state.scope) / 'evaluation.jsonl').clear()
    return {'message': 'Evaluation history cleared.'}

@api.get('/observability')
def get_observability(request: Request):
    records = load_observability_history(logs(request.state.scope) / 'observability.jsonl')
    return {'runs': records, 'summary': calculate_observability_summary(records)}

@api.delete('/observability')
def clear_observability(request: Request):
    clear_observability_history(logs(request.state.scope) / 'observability.jsonl')
    return {'message': 'Observability history cleared.'}

# Compatibility helpers used by older tests and callers; metadata still uses DB truth.
is_harmful_request = harmful

def build_knowledge_base_answer(question, scope=None):
    if intent_for(question) != 'metadata':
        return None
    if scope is None:
        return None  # Never fall back to a shared/global registry.
    return {'answer': metadata_answer(question, repository.list(scope)), 'route': 'metadata'}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run('backend.api:api', host='127.0.0.1', port=8000)

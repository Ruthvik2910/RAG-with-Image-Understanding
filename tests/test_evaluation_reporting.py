"""Focused regression tests for evaluation recording, scoping and aggregation."""
import json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import pytest
from fastapi.testclient import TestClient
from backend import api as api_module, integrations, runtime_settings as settings
from backend.evaluation import EvaluationResult, EvaluationTracker


def ask(client, question='How many documents have I uploaded?'):
    response = client.post('/chat', json={'question': question})
    assert response.status_code == 200, response.text
    return response.json()


def test_28_real_completed_requests_are_28_persisted_evaluations(setup_app):
    client, *_ = setup_app
    for _ in range(28):
        ask(client)
    response = client.get('/evaluation').json()
    assert response['summary']['total_questions'] == 28
    assert response['summary']['successful_questions'] == 28
    assert len({r['request_id'] for r in response['results']}) == 28
    assert client.get('/observability').json()['summary']['total_runs'] == 28
    # Refresh is read-only and sees the next actual request, never duplicates it.
    assert client.get('/evaluation').json()['summary']['total_questions'] == 28
    ask(client)
    assert client.get('/evaluation').json()['summary']['total_questions'] == 29


def test_hostname_mismatch_reproduces_empty_evaluation_session(setup_app):
    client, *_ = setup_app
    ask(client)
    assert client.get('/evaluation').json()['summary']['total_questions'] == 1
    # Cookie isolation reproduces the old default localhost vs 127.0.0.1 mismatch.
    other_host = TestClient(api_module.api, base_url='http://127.0.0.1:8000')
    other_host.cookies.update(client.cookies)
    assert other_host.get('/evaluation').json()['summary']['total_questions'] == 0


def test_pending_approval_is_observable_but_not_a_completed_evaluation(setup_app, monkeypatch):
    client, *_ = setup_app
    monkeypatch.setattr(settings, 'WEB_APPROVAL', True)
    pending = ask(client, 'What is the latest technology news?')
    assert pending['status'] == 'approval_required'
    assert client.get('/evaluation').json()['summary']['total_questions'] == 0
    assert client.get('/observability').json()['summary']['total_runs'] == 1
    result = client.post('/chat/approval', json={'thread_id': pending['thread_id'], 'approved': True})
    assert result.status_code == 200, result.text
    summary = client.get('/evaluation').json()['summary']
    assert summary['total_questions'] == 1 and summary['successful_questions'] == 1
    assert client.get('/observability').json()['summary']['total_runs'] == 2


def test_failure_and_failed_quality_are_distinct_from_execution_success(setup_app, monkeypatch):
    client, *_ = setup_app
    def fail(*args):
        raise integrations.ServiceUnavailable('Provider unavailable')
    monkeypatch.setattr(integrations, 'complete', fail)
    failed = client.post('/chat', json={'question': 'Explain recursion'})
    assert failed.status_code == 503
    evaluation = client.get('/evaluation').json()
    assert evaluation['summary']['failed_questions'] == 1
    assert evaluation['results'][0]['success'] is False
    assert evaluation['results'][0]['status'] == 'failed'
    assert evaluation['results'][0]['grounded'] is None
    assert evaluation['summary']['grounded_evaluated_questions'] == 0
    # A completed answer failing quality is still an executed request, not an HTTP failure.
    with api_module.repository.connect() as db:
        scope = db.execute('select value from settings where key=?', ('session_secret',)).fetchone()
    tracker = EvaluationTracker(__import__('pathlib').Path(setup_app[1].root) / 'separate-evaluation.jsonl')
    tracker.add(EvaluationResult(question='Question', answer='Answer', grounded=False, answers_question=False))
    assert tracker.summary()['successful_questions'] == 1
    assert tracker.summary()['answer_quality_rate'] == 0


def test_clear_evaluation_does_not_clear_observability_or_other_sessions(setup_app):
    client, *_ = setup_app
    ask(client)
    other = TestClient(api_module.api)
    ask(other)
    assert client.delete('/evaluation').status_code == 200
    assert client.get('/evaluation').json()['summary']['total_questions'] == 0
    assert client.get('/evaluation').json()['results'] == []
    assert client.get('/observability').json()['summary']['total_runs'] == 1
    assert other.get('/evaluation').json()['summary']['total_questions'] == 1


def test_independent_metric_writers_do_not_suppress_evaluation(setup_app, monkeypatch):
    client, *_ = setup_app
    def unavailable(*args, **kwargs):
        raise OSError('simulated observer disk failure')
    monkeypatch.setattr(api_module.RunObserver, 'record', unavailable)
    ask(client)
    assert client.get('/evaluation').json()['summary']['total_questions'] == 1


def test_failed_evaluation_write_does_not_break_request_or_fake_records(setup_app, monkeypatch):
    client, *_ = setup_app
    def unavailable(*args, **kwargs):
        raise OSError('simulated evaluation disk failure')
    monkeypatch.setattr(EvaluationTracker, 'add', unavailable)
    ask(client)
    assert client.get('/evaluation').json()['summary']['total_questions'] == 0
    assert client.get('/observability').json()['summary']['total_runs'] == 1


def test_metrics_ignore_unknown_assessments_and_nonretrieval_requests(tmp_path):
    tracker = EvaluationTracker(tmp_path / 'eval.jsonl')
    tracker.add(EvaluationResult('A', 'A', retrieved_documents=4, relevant_documents=2, grounded=True, answers_question=True, latency_seconds=1, retry_count=0))
    tracker.add(EvaluationResult('B', 'B', retrieved_documents=2, relevant_documents=0, grounded=False, answers_question=True, latency_seconds=3, retry_count=2))
    tracker.add(EvaluationResult('C', 'C', grounded=None, answers_question=None, latency_seconds=float('nan'), retry_count=-5, error='provider failed'))
    summary = EvaluationTracker(tracker.storage_path).summary()
    assert summary['total_questions'] == 3
    assert summary['successful_questions'] == 2 and summary['failed_questions'] == 1
    assert summary['grounded_rate'] == 0.5
    assert summary['answer_quality_rate'] == 1
    assert summary['average_retrieval_relevance'] == 0.25
    assert summary['average_latency_seconds'] == 2
    assert summary['average_retries'] == 1
    assert summary['grounded_evaluated_questions'] == 2
    assert summary['retrieval_evaluated_questions'] == 2


def test_legacy_false_success_is_not_discarded_and_unknown_fields_are_tolerated(tmp_path):
    file = tmp_path / 'eval.jsonl'
    rows = [
        {'question':'failed', 'answer':'', 'success':False, 'error':'', 'future_field':'ignored'},
        {'question':'pending', 'answer':'', 'status':'approval_required'},
        {'question':'passed', 'answer':'answer', 'success':True, 'grounded':'false', 'answers_question':'true'},
    ]
    file.write_text('\n'.join(json.dumps(row) for row in rows) + '\ncorrupt json\n')
    tracker = EvaluationTracker(file)
    assert len(tracker.results) == 2
    assert tracker.summary()['successful_questions'] == 1
    assert tracker.summary()['failed_questions'] == 1
    assert tracker.results[1].grounded is False


def test_same_request_id_cannot_be_appended_twice_even_concurrently(tmp_path):
    file = tmp_path / 'eval.jsonl'
    def write(_):
        EvaluationTracker(file).add(EvaluationResult('Q', 'A', request_id='same-run'))
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(write, range(12)))
    assert len(file.read_text().splitlines()) == 1
    assert EvaluationTracker(file).summary()['total_questions'] == 1


def test_empty_history_has_genuine_zero_metrics(tmp_path):
    tracker = EvaluationTracker(tmp_path / 'missing.jsonl')
    assert all(value == 0 for value in tracker.summary().values())
    tracker.clear()
    assert tracker.results == []


def test_approval_failure_records_one_failed_evaluation(setup_app, monkeypatch):
    client, *_ = setup_app
    monkeypatch.setattr(settings, 'WEB_APPROVAL', True)
    pending = ask(client, 'Latest technology news')
    def fail(*args):
        raise integrations.ServiceUnavailable('Search unavailable')
    monkeypatch.setattr(integrations, 'search_web', fail)
    response = client.post('/chat/approval', json={'thread_id':pending['thread_id'], 'approved':True})
    assert response.status_code == 503
    data = client.get('/evaluation').json()
    assert data['summary']['total_questions'] == data['summary']['failed_questions'] == 1
    assert data['results'][0]['question'] == 'Latest technology news'


def test_terminal_image_errors_are_recorded_without_changing_vision_behavior(setup_app, monkeypatch):
    from PIL import Image
    from backend import image_search
    client, *_ = setup_app
    out = BytesIO()
    Image.new('RGB', (20, 20), 'blue').save(out, format='PNG')
    def fail(*args):
        raise integrations.ServiceUnavailable('Vision unavailable')
    monkeypatch.setattr(image_search, 'answer_image_question', fail)
    result = client.post('/chat/image', data={'question':'Describe'}, files={'image':('a.png',out.getvalue(),'image/png')})
    assert result.status_code == 503
    assert client.get('/evaluation').json()['summary']['failed_questions'] == 1


def test_clearing_evaluation_does_not_delete_observability_and_future_data_reappears(setup_app):
    client, *_ = setup_app
    ask(client)
    observed = client.get('/observability').json()
    assert client.delete('/evaluation').status_code == 200
    assert client.get('/evaluation').json()['summary']['total_questions'] == 0
    assert client.get('/observability').json() == observed
    ask(client)
    assert client.get('/evaluation').json()['summary']['total_questions'] == 1
    assert client.get('/observability').json()['summary']['total_runs'] == 2


def test_direct_execution_success_does_not_fabricate_a_quality_grade(setup_app):
    client, *_ = setup_app
    ask(client, 'What is Java?')
    data = client.get('/evaluation').json()
    assert data['summary']['successful_questions'] == 1
    assert data['summary']['grounded_evaluated_questions'] == 0
    assert data['summary']['quality_evaluated_questions'] == 0
    assert data['results'][0]['grounded'] is None
    assert data['results'][0]['answers_question'] is None


def test_tracker_rejects_nonterminal_rows_before_writing(tmp_path):
    file = tmp_path / 'eval.jsonl'
    tracker = EvaluationTracker(file)
    tracker.add(EvaluationResult('Waiting', '', status='approval_required'))
    assert not file.exists()
    assert tracker.summary()['total_questions'] == 0


def test_http_200_with_explicit_execution_failure_is_not_counted_as_success(setup_app, monkeypatch):
    client, _, _, service, *_ = setup_app
    monkeypatch.setattr(service, 'run', lambda *args: {'answer':'Execution failed.', 'route':'local', 'success':False})
    monkeypatch.setattr(service, 'forget', lambda *args: None)
    response = client.post('/chat', json={'question':'A request that failed during execution'})
    assert response.status_code == 200
    data = client.get('/evaluation').json()
    assert data['summary']['successful_questions'] == 0
    assert data['summary']['failed_questions'] == 1
    assert data['results'][0]['status'] == 'failed'
    assert data['results'][0]['success'] is False

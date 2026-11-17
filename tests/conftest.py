import os
import tempfile
from pathlib import Path
# Never touch user databases or credentials in deterministic tests.
os.environ['RAG_DATA_DIR'] = tempfile.mkdtemp(prefix='rag-suite-')
os.environ.setdefault('GROQ_API_KEY', 'offline-placeholder-not-a-real-key')
os.environ.setdefault('GOOGLE_API_KEY', 'offline-placeholder-not-a-real-key')
os.environ.setdefault('TAVILY_API_KEY', 'offline-placeholder-not-a-real-key')
import pytest
from fastapi.testclient import TestClient
from backend import api as api_module
from backend import integrations, runtime_settings as settings
from backend.repository import Repository, chunks_for
from backend.orchestration import Orchestrator

class MemoryIndex:
    def __init__(self):
        self.rows = {}
        self.search_calls = []
        self.fail = False
    def add(self, row):
        if self.fail:
            raise integrations.ServiceUnavailable('Embedding/indexing failed. Retry indexing.')
        pieces = list(chunks_for(row))
        self.rows[row['scope'], row['document_id']] = pieces
        return len(pieces)
    def delete(self, scope, identifier):
        self.rows.pop((scope, identifier), None)
    def search(self, scope, question, document_ids=None):
        self.search_calls.append((scope, question, document_ids))
        return [p for (s, d), pieces in self.rows.items() if s == scope and (not document_ids or d in document_ids) for p in pieces[:4]]

@pytest.fixture
def setup_app(tmp_path, monkeypatch):
    repo = Repository(tmp_path)
    index = MemoryIndex()
    service = Orchestrator(repo, index)
    monkeypatch.setattr(api_module, 'repository', repo)
    monkeypatch.setattr(api_module, 'rag_service', service)
    monkeypatch.setattr(settings, 'WEB_APPROVAL', False)
    calls = []
    def completion(system, question):
        calls.append((system, question))
        if 'Assess whether' in system:
            return '{"unsafe":false}'
        if 'Choose a route' in system:
            return '{"route":"local"}'
        if 'Check' in system and ('supported' in system or 'support' in system):
            return '{"grounded":true,"answers_question":true}'
        return 'Generated explanation based on the evidence.'
    monkeypatch.setattr(integrations, 'complete', completion)
    web_calls = []
    def web(question):
        web_calls.append(question)
        return [{'text': 'A recent technology event.', 'metadata': {'source': 'web', 'title': 'Source', 'url': 'https://example.org/news'}}]
    monkeypatch.setattr(integrations, 'search_web', web)
    with TestClient(api_module.api) as client:
        yield client, repo, index, service, calls, web_calls

@pytest.fixture
def java_text():
    return (Path(__file__).parent / 'fixtures/Dynamic_Programming.txt').read_bytes()

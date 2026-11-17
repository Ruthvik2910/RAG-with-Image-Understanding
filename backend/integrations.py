"""Lazy provider adapters. Ordinary completions never require a tool call."""
import json
import os
from functools import lru_cache
from backend import runtime_settings as settings
from backend.repository import chunks_for

class ServiceUnavailable(RuntimeError):
    pass


def require_key(name):
    if not os.getenv(name):
        raise ServiceUnavailable(f'{name} is not configured. Add it to your .env and restart the backend.')


def response_text(message):
    if isinstance(message, str):
        return message
    if isinstance(message.content, list):
        return '\n'.join(str(b.get('text', '')) for b in message.content if isinstance(b, dict))
    return str(message.content or '')


@lru_cache(maxsize=1)
def get_llm():
    require_key('GROQ_API_KEY')
    from langchain_groq import ChatGroq
    return ChatGroq(model=os.getenv('LLM_MODEL', 'openai/gpt-oss-120b'),
                    api_key=os.environ['GROQ_API_KEY'],
                    temperature=float(os.getenv('LLM_TEMPERATURE', '0.2')),
                    max_retries=int(os.getenv('LLM_MAX_RETRIES', '2')), timeout=60)


def complete(system, question):
    from langchain_core.messages import SystemMessage, HumanMessage
    try:
        # No tools are bound, so a refusal or normal prose is always valid output.
        return response_text(get_llm().invoke([SystemMessage(content=system), HumanMessage(content=question)]))
    except ServiceUnavailable:
        raise
    except Exception as exc:
        # Compatibility with providers returning a refusal in tool_use_failed.
        body = getattr(exc, 'body', {})
        error = body.get('error', body) if isinstance(body, dict) else {}
        failed = str(error.get('failed_generation', ''))
        if error.get('code') == 'tool_use_failed' and is_refusal(failed):
            return failed
        raise ServiceUnavailable('The language model is unavailable. Check the model, API key, and provider quota.') from exc


def is_refusal(text):
    import re
    return bool(re.search(r"\b(?:I (?:cannot|can't|won't|am unable to)|I'm (?:sorry|unable))\b", text, re.I))


def json_result(system, question):
    raw = complete(system + '\nReturn only a JSON object. Do not call tools.', question).strip()
    if is_refusal(raw):
        return {'refusal': raw}
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    try:
        result = json.loads(raw)
        return result if isinstance(result, dict) else {}
    except json.JSONDecodeError:
        return {}


@lru_cache(maxsize=1)
def vector_store():
    require_key('GOOGLE_API_KEY')
    from langchain_google_genai import GoogleGenerativeAIEmbeddings
    from langchain_chroma import Chroma
    embed = GoogleGenerativeAIEmbeddings(model=os.getenv('EMBEDDING_MODEL', 'gemini-embedding-001'),
                                         google_api_key=os.environ['GOOGLE_API_KEY'])
    return Chroma(collection_name=os.getenv('SCOPED_COLLECTION_NAME', 'rag-scoped-v2'),
                  persist_directory=str(settings.DATA_DIR / 'vectors'), embedding_function=embed)


class VectorIndex:
    def add(self, row):
        from langchain_core.documents import Document
        store = vector_store()
        pieces = list(chunks_for(row))
        ids = [f"{row['scope']}:{row['document_id']}:{i}" for i in range(len(pieces))]
        try:
            for start in range(0, len(pieces), 64):
                batch = pieces[start:start + 64]
                store.add_documents([Document(page_content=p['text'], metadata=p['metadata']) for p in batch], ids=ids[start:start + 64])
        except Exception as exc:
            # Deterministic upserts make a retry safe after a partial provider failure.
            raise ServiceUnavailable('Embedding/indexing failed. Your extracted document is saved; retry indexing.') from exc
        return len(pieces)

    def delete(self, scope, identifier):
        # Deleting never needs an embedding API call or even an API key.
        import chromadb
        try:
            client = chromadb.PersistentClient(path=str(settings.DATA_DIR / 'vectors'))
            collection = client.get_collection(os.getenv('SCOPED_COLLECTION_NAME', 'rag-scoped-v2'))
        except Exception as exc:
            if 'does not exist' in str(exc).lower() or 'not found' in str(exc).lower():
                return
            raise
        collection.delete(where={'$and': [{'scope': scope}, {'document_id': identifier}]})

    def search(self, scope, question, document_ids=None):
        filters = [{'scope': scope}]
        if document_ids:
            filters.append({'document_id': {'$in': document_ids}})
        where = filters[0] if len(filters) == 1 else {'$and': filters}
        try:
            # Search each named document independently for fair multi-document coverage.
            groups = document_ids or [None]
            result = []
            for identifier in groups:
                selected = {'$and': [{'scope': scope}, {'document_id': identifier}]} if identifier else where
                docs = vector_store().similarity_search(question, k=4, filter=selected)
                result.extend({'text': d.page_content, 'metadata': d.metadata} for d in docs)
            return result
        except ServiceUnavailable:
            raise
        except Exception as exc:
            raise ServiceUnavailable('Local retrieval failed. Check embeddings and retry indexing; no web search was run.') from exc


def search_web(question):
    require_key('TAVILY_API_KEY')
    from tavily import TavilyClient
    try:
        response = TavilyClient(api_key=os.environ['TAVILY_API_KEY']).search(query=question, max_results=5)
        return [{'text': r['content'], 'metadata': {'source': 'web', 'url': r['url'], 'title': r.get('title', r['url'])}}
                for r in response.get('results', []) if r.get('content') and str(r.get('url', '')).startswith(('https://', 'http://'))]
    except Exception as exc:
        raise ServiceUnavailable('Web search is unavailable. Check the Tavily API key and service quota.') from exc

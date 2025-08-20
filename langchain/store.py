import os.path
from pathlib import Path

from langchain.retrievers import MultiVectorRetriever
from langchain_chroma import Chroma
from langchain_community.storage import SQLStore
from langchain_ollama import OllamaEmbeddings

from whoosh import scoring
from whoosh.fields import Schema, ID, TEXT
from whoosh.index import create_in, open_dir
from whoosh.qparser import MultifieldParser

ID_KEY = 'doc_id'
# EMBEDDINGS_MODEL = "nomic-embed-text"
EMBEDDINGS_MODEL = "mxbai-embed-large"


def get_retriever(chroma_store_path: str | Path = 'chroma_store',
                  document_db_path: str | Path = 'doc_store.db', num_docs=4) -> MultiVectorRetriever:
    sqlstore = SQLStore(namespace="docs", db_url=f'sqlite:///{Path(document_db_path).resolve()}')
    sqlstore.create_schema()
    return MultiVectorRetriever(
        vectorstore=Chroma(persist_directory=str(chroma_store_path),
                           embedding_function=OllamaEmbeddings(model="nomic-embed-text")),
        byte_store=sqlstore,
        id_key=ID_KEY,
        search_kwargs={
            'k': num_docs,
        },
    )


class SearchIndex:
    def __init__(self, search_index_path: str | Path = 'whoosh_index'):
        self.schema = Schema(**{
            ID_KEY: ID(stored=True, unique=True),
            'title': TEXT(stored=True),
            'text': TEXT(stored=False)
        })

        if not os.path.exists(search_index_path):
            os.makedirs(search_index_path, exist_ok=True)
            self.index = create_in(search_index_path, self.schema)
        else:
            self.index = open_dir(search_index_path)
        self.query_parser = MultifieldParser(["title", "text"], schema=self.index.schema,
                                             fieldboosts={"title": 4.0, "text": 1.0})

    def write(self, documents: dict | list[dict]):
        if not isinstance(documents, list):
            documents = [documents]
        writer = self.index.writer()
        for document in documents:
            writer.update_document(**document)
        writer.commit()

    def search(self, q):
        with self.index.searcher(weighting=scoring.BM25F()) as searcher:
            query = self.query_parser.parse(q)
            hits = searcher.search(query, limit=20)
            return [{**hit, 'score': hit.score} for hit in hits]

    def delete(self, doc_ids: str | list[str]):
        if isinstance(doc_ids, str):
            doc_ids = [doc_ids]
        writer = self.index.writer()
        for doc_id in doc_ids:
            writer.delete_by_term(ID_KEY, doc_id)
        writer.commit()


from pathlib import Path

from langchain.retrievers import MultiVectorRetriever
from langchain_chroma import Chroma
from langchain_community.storage import SQLStore
from langchain_ollama import OllamaEmbeddings

ID_KEY = 'id_key'

def get_retriever(chroma_store_path: str, document_db_path: str, num_docs=4) -> MultiVectorRetriever:
    sqlstore = SQLStore(namespace="docs", db_url=f'sqlite:///{Path(document_db_path).resolve()}')
    sqlstore.create_schema()
    return MultiVectorRetriever(
        vectorstore=Chroma(persist_directory=chroma_store_path,
                           embedding_function=OllamaEmbeddings(model="nomic-embed-text")),
        byte_store=sqlstore,
        id_key=ID_KEY,
        search_kwargs={
            'k': num_docs,
        },
    )

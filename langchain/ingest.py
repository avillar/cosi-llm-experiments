import argparse
import hashlib
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Generator

from langchain.retrievers import MultiVectorRetriever
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from langchain_unstructured.document_loaders import UnstructuredLoader
from unstructured.cleaners.core import remove_punctuation, clean, clean_extra_whitespace

from doc_utils import extract_title, Summarizer
from store import get_retriever, ID_KEY

#DOC_ELEMENT_CATEGORIES = {"NarrativeText", "ListItem", "Title"}
DOC_ELEMENT_CATEGORIES = {"NarrativeText"}

def hash_file(path: Path | str) -> str:
    file_hash = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(8192), b''):
            file_hash.update(block)
    return file_hash.hexdigest()


def load_documents(folder: Path, retriever: MultiVectorRetriever,
                   summary_llm: str | None = None, force=False) -> Generator[tuple[Document, Any], Any, None]:
    all_files = [f for f in folder.rglob("*") if f.is_file() and not f.name.startswith(".")]
    summarizer = Summarizer(summary_llm) if summary_llm else None
    for idx, file in enumerate(all_files):
        print("Processing", file)
        try:
            file_hash = hash_file(file)
            doc_id = str(file)

            existing = retriever.docstore.mget([doc_id])[0]
            if not force and existing and existing.metadata.get("hash") == file_hash:
                print(f" > Skipping unchanged document: {file}")
                continue

            doc, summary = load_document(file, doc_id, file_hash, summarizer)
            print(f' > Title for {doc.metadata["source"]}: {doc.metadata["title"]}')
            yield doc, summary
        except Exception as e:
            print(f" > Error found while processing {file}: {e}", file=sys.stderr)
        finally:
            print(f"Processed {idx + 1}/{len(all_files)} ({(idx + 1) * 100 / len(all_files):.2f})%)")



def load_document(file: Path, doc_id: str, file_hash: str, summarizer: Summarizer | str = None):
    if isinstance(summarizer, str):
        summarizer = Summarizer(summarizer)
    loader = UnstructuredLoader(
        file,
        post_processors=[clean, remove_punctuation, clean_extra_whitespace],
    )
    loaded = loader.load()
    selected_elements = [e for e in loaded if e.metadata['category'] in DOC_ELEMENT_CATEGORIES]
    full_text = " ".join([e.page_content for e in selected_elements])
    doc = Document(page_content=full_text, metadata={
        ID_KEY: doc_id,
        "source": str(file),
        "title": extract_title(full_text),
        "doc_type": "full",
        "collection": str(file.parent),
        "hash": file_hash,
    })
    summary = summarizer.summarize(full_text) if summarizer else None
    return doc, summary


def ingest_docs(kb_path: str, chroma_store_path: str, document_db_path: str,
                summary_llm='gemma3:12b', force=False):
    if force:
        if os.path.exists(chroma_store_path):
            shutil.rmtree(chroma_store_path)
        Path(document_db_path).unlink(missing_ok=True)

    retriever = get_retriever(chroma_store_path, document_db_path)
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=100)

    seen_ids = set()

    for doc, summary in load_documents(Path(kb_path), retriever, summary_llm, force=force):
        seen_ids.add(doc.metadata[ID_KEY])

        if summary:
            retriever.vectorstore.add_documents([Document(page_content=summary, metadata={
                'doc_type': 'summary',
                ID_KEY: doc.metadata[ID_KEY],
            })])

        chunks = []
        for chunk in splitter.split_documents([doc]):
            chunk.metadata[ID_KEY] = doc.metadata[ID_KEY]
            chunk.metadata['doc_type'] = 'chunk'
            chunks.append(chunk)

        retriever.vectorstore.delete(where={ID_KEY: doc.metadata[ID_KEY]})
        retriever.docstore.mdelete([doc.metadata[ID_KEY]])
        retriever.vectorstore.add_documents(chunks)
        retriever.docstore.mset([(doc.metadata[ID_KEY], doc)])

    # Delete old documents
    existing_ids = set(retriever.docstore.yield_keys())
    old_ids = list(existing_ids - seen_ids)
    for old_id in old_ids:
        print(f"Removing old document ID {old_id}")
        retriever.vectorstore.delete(where={ID_KEY: old_id})
    retriever.docstore.mdelete(old_ids)


def _main():
    parser = argparse.ArgumentParser(description="Ingest documents into Chroma vector store.")
    parser.add_argument("--kb", type=str, default='kb', help="Path to the knowledge base folder.")
    parser.add_argument("--vector-db", type=str, default='chroma_store',
                        help="Path to the Chroma store directory.")
    parser.add_argument("--document-db", type=str, default='doc_store.db',
                        help="Path to the SQLite Document database.")
    parser.add_argument("--summary-llm", type=str, default='gemma3:12b',
                        help="LLM to use for generating document summaries ('none' for disabling summaries).")
    parser.add_argument('-f', '--force', action='store_true', default=False,
                        help="Force parsing documents even if they have not changed.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.ERROR)

    ingest_docs(kb_path=args.kb,
                chroma_store_path=args.vector_db,
                document_db_path=args.document_db,
                summary_llm=args.summary_llm if args.summary_llm != 'none' else None,
                force=args.force)


if __name__ == "__main__":
    _main()

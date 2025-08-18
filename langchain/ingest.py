import argparse
import os
import shutil
from pathlib import Path
from typing import Any, Generator

from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from langchain_unstructured.document_loaders import UnstructuredLoader
from unstructured.cleaners.core import remove_punctuation, clean, clean_extra_whitespace

from doc_utils import extract_title, Summarizer
from store import get_retriever, ID_KEY

#DOC_ELEMENT_CATEGORIES = {"NarrativeText", "ListItem", "Title"}
DOC_ELEMENT_CATEGORIES = {"NarrativeText"}


def load_documents(folder: Path, summary_llm: str | None = None) -> Generator[tuple[Document, Any], Any, None]:
    all_files = [f for f in folder.rglob("*") if f.is_file() and not f.name.startswith(".")]
    summarizer = Summarizer(summary_llm) if summary_llm else None
    for idx, file in enumerate(all_files):
        print("Processing", file)
        doc, summary = load_document(file, summarizer)
        print(f'Title for {doc.metadata["source"]}: {doc.metadata["title"]}')
        print(f"Processed {idx + 1}/{len(all_files)} ({(idx + 1) * 100 / len(all_files):.2f})%)")
        yield doc, summary


def load_document(file: Path, summarizer: Summarizer | str = None):
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
        ID_KEY: str(file),
        "source": str(file),
        "title": extract_title(full_text),
        "doc_type": "full",
        "dir": str(file.parent),
    })
    summary = summarizer.summarize(full_text) if summarizer else None
    return doc, summary


def ingest_docs(kb_path: str, chroma_store_path: str, document_db_path: str,
                summary_llm='gemma3:12b'):
    if os.path.exists(chroma_store_path):
        shutil.rmtree(chroma_store_path)
    Path(document_db_path).unlink(missing_ok=True)

    retriever = get_retriever(chroma_store_path, document_db_path)

    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=100)
    for doc, summary in load_documents(Path(kb_path), summary_llm):
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
        retriever.vectorstore.add_documents(chunks)
        retriever.docstore.mset([(doc.metadata[ID_KEY], doc)])


def _main():
    parser = argparse.ArgumentParser(description="Ingest documents into Chroma vector store.")
    parser.add_argument("--kb", type=str, default='kb', help="Path to the knowledge base folder.")
    parser.add_argument("--vector-db", type=str, default='chroma_store',
                        help="Path to the Chroma store directory.")
    parser.add_argument("--document-db", type=str, default='doc_store.db',
                        help="Path to the SQLite Document database.")
    parser.add_argument("--summary-llm", type=str, default='gemma3:12b',
                        help="LLM to use for generating document summaries ('none' for disabling summaries).")
    args = parser.parse_args()

    ingest_docs(kb_path=args.kb,
                chroma_store_path=args.vector_db,
                document_db_path=args.document_db,
                summary_llm=args.summary_llm if args.summary_llm != 'none' else None)


if __name__ == "__main__":
    _main()

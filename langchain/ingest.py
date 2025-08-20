import argparse
import datetime
import hashlib
import logging
import sys
from pathlib import Path
from typing import Any, Generator

from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from langchain_core.prompts import PromptTemplate
from langchain_unstructured.document_loaders import UnstructuredLoader
from unstructured.cleaners.core import remove_punctuation, clean, clean_extra_whitespace

from doc_utils import Summarizer
from store import get_retriever, ID_KEY, SearchIndex

DOC_ELEMENT_CATEGORIES = {"NarrativeText", "ListItem", "Title"}

SUMMARIZER_PROMPT = PromptTemplate(input_variables=["text"], template='''Write a summary of the following text, \
focusing especially on OGC mentions, contributions and standards, \
and relevance for the OGC and the geospatial community. Do not \
include any introduction or additional text, just the summary. Do not\
mention the text itself, just summarize the contents:

### TEXT

"{text}"

''')


def hash_file(path: Path | str) -> str:
    file_hash = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(8192), b''):
            file_hash.update(block)
    return file_hash.hexdigest()


class DocumentIngester:
    def __init__(self, kb_path: str, chroma_store_path: str, document_db_path: str,
                 search_index_path: str, summaries=True, llm='gemma3:12b', llm_provider='ollama', force=False):
        self.kb_path = Path(kb_path)
        self.chroma_store_path = chroma_store_path
        self.document_db_path = document_db_path
        self.llm = llm
        self.llm_provider = llm_provider
        self.force = force
        self.summaries = summaries

        self.retriever = get_retriever(self.chroma_store_path, self.document_db_path)
        self.summarizer = Summarizer(model=self.llm, llm_provider=self.llm_provider)
        self.search_index = SearchIndex(search_index_path)

    def _load_documents(self) -> Generator[
        tuple[str, Document | None, Any], Any, None]:
        all_files = [f for f in self.kb_path.rglob("*") if f.is_file() and not f.name.startswith(".")]
        for idx, file in enumerate(all_files):
            print("Processing", file)
            try:
                file_hash = hash_file(file)
                doc_id = str(file)

                existing = self.retriever.docstore.mget([doc_id])[0]
                if not self.force:
                    if existing:
                        if existing.metadata.get("hash") == file_hash:
                            print(f" > Skipping unchanged document: {file}")
                            yield doc_id, None, None
                            continue
                        else:
                            print(f" > File has changed ({existing.metadata.get('hash')} vs {file_hash})")
                    else:
                        print(" > Adding new document")

                doc, summary = self._load_document(file, doc_id, file_hash)
                print(f' > Title for {doc.metadata["source"]}: {doc.metadata["title"]}')
                yield doc_id, doc, summary
            except Exception as e:
                print(f" > Error found while processing {file}: {e}", file=sys.stderr)
                import traceback
                traceback.print_exc()
            finally:
                print(f"Processed {idx + 1}/{len(all_files)} ({(idx + 1) * 100 / len(all_files):.2f}%)")

    def _load_document(self, file: Path, doc_id: str, file_hash: str):
        loader = UnstructuredLoader(
            file,
            post_processors=[clean, remove_punctuation, clean_extra_whitespace],
        )
        loaded = loader.load()
        selected_elements = []
        for e in loaded:
            cat = e.metadata['category']
            if cat not in DOC_ELEMENT_CATEGORIES:
                continue
            content = e.page_content.strip()
            if cat == 'Title':
                selected_elements.append(f"\n\n### {content}\n\n")
            else:
                selected_elements.append(content)

        full_text = " ".join(selected_elements).strip()
        print(" > Extracting title")
        doc_title = self.summarizer.extract_title(full_text)
        doc = Document(page_content=full_text, metadata={
            ID_KEY: doc_id,
            "source": str(file),
            "title": doc_title,
            "doc_type": "full",
            "collection": str(file.parent),
            "hash": file_hash,
            "added": datetime.datetime.now().isoformat(),
        })
        summary = None
        if self.summaries and full_text:
            print(' > Summarizing document')
            summary = self.summarizer.summarize(full_text)
        return doc, summary

    def process_documents(self):
        splitter = RecursiveCharacterTextSplitter(chunk_size=900, chunk_overlap=100)
        seen_ids = set()
        for doc_id, doc, summary in self._load_documents():
            seen_ids.add(doc_id)
            if not doc:
                continue

            if summary:
                self.retriever.vectorstore.add_documents([Document(page_content=summary, metadata={
                    'doc_type': 'summary',
                    ID_KEY: doc.metadata[ID_KEY],
                })])

            chunks = []
            for chunk in splitter.split_documents([doc]):
                chunk.metadata[ID_KEY] = doc.metadata[ID_KEY]
                chunk.metadata['doc_type'] = 'chunk'
                chunk.page_content = f"# {doc.metadata['title']}\n\n{chunk.page_content}"
                chunks.append(chunk)

            self.retriever.vectorstore.delete(where={ID_KEY: doc.metadata[ID_KEY]})
            self.retriever.docstore.mdelete([doc.metadata[ID_KEY]])
            self.retriever.vectorstore.add_documents(chunks)
            self.retriever.docstore.mset([(doc.metadata[ID_KEY], doc)])
            self.search_index.write({
                ID_KEY: doc.metadata[ID_KEY],
                'title': doc.metadata['title'],
                'text': doc.page_content,
            })
            print(f" > Saved {doc_id}")

        # Delete old documents
        existing_ids = set(self.retriever.docstore.yield_keys())
        old_ids = list(existing_ids - seen_ids)
        for old_id in old_ids:
            print(f"Removing old document ID {old_id}")
            self.retriever.vectorstore.delete(where={ID_KEY: old_id})
        self.retriever.docstore.mdelete(old_ids)
        self.search_index.delete(old_ids)


def _main():
    parser = argparse.ArgumentParser(description="Ingest documents into Chroma vector store.")
    parser.add_argument("--kb", type=str, default='kb', help="Path to the knowledge base folder.")
    parser.add_argument("--vector-db", type=str, default='chroma_store',
                        help="Path to the Chroma store directory.")
    parser.add_argument("--document-db", type=str, default='doc_store.db',
                        help="Path to the SQLite Document database.")
    parser.add_argument("--search-index-db", type=str, default='whoosh_index',
                        help="Path to the search index directory")
    parser.add_argument("--llm", type=str, default='gemma3:12b',
                        help="LLM to use for titles and summaries.")
    parser.add_argument("--llm-provider", type=str, default='ollama', choices=['ollama', 'openai'],
                        help="LLM provider to use.")
    parser.add_argument("--disable-summaries", action="store_true", help="Disable summaries.")
    parser.add_argument('-f', '--force', action='store_true', default=False,
                        help="Force parsing documents even if they have not changed.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.ERROR)

    DocumentIngester(
        kb_path=args.kb,
        chroma_store_path=args.vector_db,
        document_db_path=args.document_db,
        search_index_path=args.search_index_db,
        llm=args.llm,
        llm_provider=args.llm_provider,
        force=args.force,
        summaries=not args.disable_summaries,
    ).process_documents()


if __name__ == "__main__":
    _main()

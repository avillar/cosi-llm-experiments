import argparse
import logging
import sys

from langchain.chains import RetrievalQA
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain.chains.retrieval import create_retrieval_chain
from langchain.retrievers import MultiVectorRetriever
from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.documents import Document
from langchain_core.globals import set_debug
from langchain_core.messages import AIMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.retrievers import RetrieverOutputLike
from langchain_core.runnables import RunnableConfig, RunnableBranch, RunnableLambda, Runnable
from langchain_ollama import ChatOllama

from store import get_retriever, ID_KEY, SearchIndex

logger = logging.getLogger(__name__)

summarization_prompt = ChatPromptTemplate.from_messages([
    ("system",
     "Summarize the following chat history in a concise paragraph. Do not include any metadata or formatting. Just the summary."),
    ("human", "{chat_history}")
])


def custom_rag_retrieve(retriever: MultiVectorRetriever, inputs,
                        keywords: list | None = None, search_index: SearchIndex | None = None) -> list[Document]:
    where = None
    if keywords and search_index:
        logger.debug("Filtering documents by keywords:", keywords)
        hit_doc_ids = [hit[ID_KEY] for hit in search_index.search(keywords)]
        where = {ID_KEY: {'$in': hit_doc_ids}}
        logger.debug('Where:', where)
    merge_filter = lambda f: {'$and': [f, where]} if where else f

    results = []
    results.extend(retriever.vectorstore.similarity_search(inputs, k=10, filter=merge_filter({'doc_type': 'chunk'})))
    results.extend(retriever.vectorstore.similarity_search(inputs, k=2, filter=merge_filter({"doc_type": "summary"})))

    all_doc_ids = list({doc.metadata[ID_KEY] for doc in results})

    logger.debug(f"== Retrieved ==\n%s", " -'" + '\n - '.join(all_doc_ids))
    return list(results)


def add_keywords(chain, q: str):
    logger.debug("Extracting keywords from", q)
    keywords = chain.invoke(q)
    return {'input': q, 'keywords': keywords}


def build_agent(llm="gemma3:12b", vector_db='chroma_store', document_db='doc_store.db',
                search_index_db='whoosh_index'):
    logger.info('Using LLM %s', llm)
    llm = ChatOllama(model=llm)
    retriever = get_retriever(chroma_store_path=vector_db,
                              document_db_path=document_db,
                              num_docs=2)

    keyword_extract_system_prompt = (
        'Extract the most relevant keywords from the following question. '
        'Return a space-separated list of the keywords, with no added text, '
        'introduction or rationale, so that it can be passed directly to a search engine. '
        'Be vague, and try to use as few keywords as possible.'
    )
    keyword_extract_prompt = ChatPromptTemplate.from_messages([
        ("system", keyword_extract_system_prompt),
        ("human", "Question: {input}\n\nKeywords:")
    ])

    base_contextualize_q_system_prompt = (
        "Formulate a standalone question as a search query for a vector database. "
        "Do NOT answer the question, just optimize it for vector document retrieval, "
        "or otherwise return it as is. Do not write any rationale or any additional text."
    )

    contextualize_q_system_prompt = (
        "Given a chat history and the latest user question "
        "which might reference context in the chat history, "
        f"{base_contextualize_q_system_prompt}"
    )
    contextualize_q_prompt_no_history = ChatPromptTemplate.from_messages(
        [
            ("system", base_contextualize_q_system_prompt),
            ("human", "Question: {input}"),
        ]
    )
    contextualize_q_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", contextualize_q_system_prompt),
            MessagesPlaceholder("chat_history"),
            ("human", "Question: {input}"),
        ]
    )

    keyword_chain = keyword_extract_prompt | llm | StrOutputParser()
    add_keywords_runnable = RunnableLambda(lambda x: add_keywords(keyword_chain, x))

    search_index = SearchIndex(search_index_path=search_index_db)

    custom_retriever = RunnableLambda(lambda x: custom_rag_retrieve(retriever=retriever,
                                                                    search_index=search_index,
                                                                    inputs=x['input'],
                                                                    keywords=x['keywords'])).with_config(
        run_name='custom_retriever'
    )
    history_aware_retriever: RetrieverOutputLike = RunnableBranch(
        (
            # Both empty string and empty list evaluate to False
            lambda x: not x.get("chat_history", False),
            # If no chat history, then we just pass input to retriever
            contextualize_q_prompt_no_history | llm | StrOutputParser() | add_keywords_runnable | custom_retriever,
        ),
        # If chat history, then we pass inputs to LLM chain, then to retriever
        contextualize_q_prompt | llm | StrOutputParser() | add_keywords_runnable | custom_retriever,
    ).with_config(run_name="chat_retriever_chain")

    qa_system_prompt = (
        "You are an assistant for question-answering tasks about the OGC. Use "
        "the following pieces of retrieved context to answer the "
        "question. If you don't know the answer, just say that you "
        "don't know. Never talk about or reference \"the context\", \"the documents\","
        " or \"the text\". Answer directly and naturally, Do not start "
        "responses with phrases like ‘The text states’ or ‘The documents reference’. "
        "Write as if you are explaining or summarizing without pointing back to the text"
        "\n\n"
        "{context}"
    )
    qa_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", qa_system_prompt),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}"),
        ]
    )
    question_answer_chain = create_stuff_documents_chain(llm, qa_prompt)
    rag_chain = create_retrieval_chain(
        history_aware_retriever, question_answer_chain
    )
    return rag_chain


def trim_history(chat_history: InMemoryChatMessageHistory, chain: Runnable, max_messages=10):
    if len(chat_history.messages) > max_messages:
        old_messages = chat_history.messages[:-max_messages]
        old_text = '\n\n'.join(f"{msg.type}: {msg.content}" for msg in old_messages)
        summary = chain.invoke({'chat_history': old_text})
        summary_message = AIMessage(content="Summary of earlier conversation: " + summary)
        chat_history.messages = [summary_message] + chat_history.messages[-max_messages:]


def _main():
    parser = argparse.ArgumentParser(description="Chat about the OGC")
    parser.add_argument('-m', '--llm', type=str, default='gemma3:12b', help="LLM to use.")
    parser.add_argument("--vector-db", type=str, default='chroma_store',
                        help="Path to the Chroma store directory.")
    parser.add_argument("--document-db", type=str, default='doc_store.db',
                        help="Path to the SQLite Document database.")
    parser.add_argument("--search-index-db", type=str, default='whoosh_index',
                        help="Path to the search index directory")
    parser.add_argument('--debug', action='store_true', help="Enable debug mode.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(levelname)s: %(message)s"
    )
    logger.setLevel(logging.DEBUG if args.debug else logging.INFO)
    if args.debug:
        set_debug(True)

    agent = build_agent(llm=args.llm, vector_db=args.vector_db, document_db=args.document_db,
                        search_index_db=args.search_index_db)

    config = RunnableConfig(**{"configurable": {"session_id": "foo"}})
    chat_history = InMemoryChatMessageHistory()
    summarization_chain = summarization_prompt | ChatOllama(model=args.llm, temperature=0.1) | StrOutputParser()
    while True:
        try:
            query = input("\nAsk a question (or type 'exit'): ")
        except (KeyboardInterrupt, EOFError):
            sys.exit(0)
        if not query.strip():
            continue
        if query.lower() == "exit":
            break

        answer_stream = agent.stream({
            'input': query,
            'ability': 'providing information on the OGC',
            'chat_history': chat_history.messages,
        }, config=config)
        print(f"\n🧠 Answer:")
        answer = ""
        for chunk in answer_stream:
            answer_chunk = chunk.get('answer')
            if answer_chunk:
                print(answer_chunk, end="", flush=True)
                answer += answer_chunk
        print()
        chat_history.add_user_message(query)
        chat_history.add_ai_message(answer)
        trim_history(chat_history, summarization_chain)


if __name__ == "__main__":
    _main()

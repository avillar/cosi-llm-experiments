from langchain.chains import RetrievalQA
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain.chains.history_aware_retriever import create_history_aware_retriever
from langchain.chains.retrieval import create_retrieval_chain
from langchain_chroma.vectorstores import Chroma
from langchain_core.chat_history import InMemoryChatMessageHistory, BaseChatMessageHistory
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableConfig
from langchain_ollama import ChatOllama, OllamaEmbeddings


def get_retriever():
    embeddings = OllamaEmbeddings(model="nomic-embed-text")
    vectorstore = Chroma(
        embedding_function=embeddings,
        persist_directory="chroma_store"
    )
    return vectorstore.as_retriever()


def get_history(session_id: str) -> BaseChatMessageHistory:
    return InMemoryChatMessageHistory()


def build_agent():
    llm = ChatOllama(model="gemma3:12b")
    retriever = get_retriever()

    contextualize_q_system_prompt = (
        "Given a chat history and the latest user question "
        "which might reference context in the chat history, "
        "formulate a standalone question which can be understood "
        "without the chat history. Do NOT answer the question, just "
        "reformulate it if needed and otherwise return it as is."
    )
    contextualize_q_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", contextualize_q_system_prompt),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}"),
        ]
    )
    history_aware_retriever = create_history_aware_retriever(
        llm, retriever, contextualize_q_prompt
    )

    qa_system_prompt = (
        "You are an assistant for question-answering tasks about the OGC. Use "
        "the following pieces of retrieved context to answer the "
        "question. If you don't know the answer, just say that you "
        "don't know. Don't talk about \"the context\", or \"the text\" that is your "
        "own internalized knowledge"
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


if __name__ == "__main__":
    agent = build_agent()
    config = RunnableConfig(**{"configurable": {"session_id": "foo"}})
    chat_history = []  # Collect chat history here (a sequence of messages)
    while True:
        query = input("\nAsk a question (or type 'exit'): ")
        if query.lower() == "exit":
            break
        answer = agent.invoke({
            'input': query,
            'ability': 'providing information on the OGC',
            'chat_history': chat_history,
        }, config=config)
        print(f"\n🧠 Answer:\n{answer['answer']}")

import argparse
import re
import sys
import time

from langchain.retrievers import MultiVectorRetriever
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import OllamaLLM

from store import get_retriever

QUESTION_RAG_PROMPT = (
    'Reformulate this question so that it can be passed to a vector store in order'
    ' to extract the most relevant documents and chunks. Add some synonyms or additional topics'
    ' to increase the quality of the results. Only return the reformulated question, without'
    ' any added text.\n\nQuestion: {input}'
)
TOPICS_RAG_PROMPT = (
    'Add a few synonyms to this topic list so that it can be used to query a vector store.'
    ' Return the new list, including the existing terms and any new ones you add. Do not'
    ' return anything else.\n\nTopics: {input}'
)


def clean_response(response):
    return re.sub(r"<think>.*?</think>\n?", "", response, flags=re.DOTALL)


def rag_fetch(retriever: MultiVectorRetriever, llm: str, question: str | None = None,
              topics: str | None = None, num_chunks=8) -> list[
    Document]:
    prompt = ChatPromptTemplate([
        ("system", "You are an assistant for document retrieval from a Vector Store."),
        ("human", QUESTION_RAG_PROMPT if question else TOPICS_RAG_PROMPT),
    ])

    llm = OllamaLLM(model=llm, temperature=0.2)
    chain = prompt | llm | StrOutputParser()

    rag_query = clean_response(chain.invoke({
        'input': question or topics,
    }))

    results: list[Document] = []
    results.extend(retriever.vectorstore.similarity_search(rag_query, k=num_chunks, filter={'doc_type': 'chunk'}))
    results.extend(retriever.vectorstore.similarity_search(rag_query, k=2, filter={"doc_type": "summary"}))

    return results


def query(llm: str, question: str | None = None, topics: str | None = None, output_format=None,
          context: list[Document] = None, num_slides=25, num_words=2500):
    if not question:
        if topics:
            question = f'Elaborate on the following topics: {topics}'
        else:
            raise ValueError("You must specify at least one of question or topics.")
    if context is None:
        context = []
    system_role = ('You are an online communication and marketing expert working for the OGC. You are skilled in'
                   ' Markdown.')
    question_prompt = ('Answer the following question as a blog post using Markdown format.'
                       ' Do not include any introductory text or call to action, just the blog post content.'
                       ' Do not reference individual documents or the context, focus on the content. '
                       f' Aim for around {num_words} words. Delve as deep as you see fit.')
    if output_format == 'slides':
        system_role = ('You are a researcher and domain expert working for the OGC,'
                       ' about to present your work at a conference')
        question_prompt = ('Answer the following question as a set of PowerPoint slides using Markdown format.'
                           ' Include references to useful images or diagrams to accompany the slides'
                           ' (or if the slide is an image/diagram only), but only when necessary.'
                           ' Use a combination bullet points and text, do not make the bullet points too concise '
                           ' and try to include examples. '
                           ' Do not be afraid to have some text-heavy slides, we want a stand-alone presentation. '
                           ' Do not reference individual documents or the context, focus on the content. '
                           ' Do not include any introductory text or call to action, just the content for the slides.'
                           f' Aim for at least {num_slides} slides, but no more than {int(num_slides * 1.3)}.'
                           f' Delve as deep as you see fit.')
    prompt = ChatPromptTemplate([
        ("system", system_role),
        ("human", 'Given the following context:\n\n{context}\n\n{question_prompt}\n\nQuestion: {question}')
    ])

    llm = OllamaLLM(model=llm)
    chain = prompt | llm | StrOutputParser()

    return clean_response(chain.invoke({
        #'context': ("\n\n".join(f"## {doc.metadata['title']}\n\n{doc.page_content}" for doc in context))[:100_000],
        'context': ("\n\n".join(doc.page_content for doc in context))[:100_000],
        'question_prompt': question_prompt,
        'question': question,
    }))


def _main():
    parser = argparse.ArgumentParser(description="OGC LLM generator")
    parser.add_argument("-m", '--llm', type=str, default='gemma3:12b', help="LLM to use.")
    parser.add_argument("--vector-db", type=str, default='chroma_store',
                        help="Path to the Chroma store directory.")
    parser.add_argument("--document-db", type=str, default='doc_store.db',
                        help="Path to the SQLite Document database.")
    parser.add_argument("-f", '--output-format', choices=['blog', 'slides'], help="Output format.")
    parser.add_argument('--num-docs', type=int, default=8, help="Number of RAG chunks to retrieve.")
    parser.add_argument('--num-slides', type=int, default=25, help="Number of slides to generate.")
    parser.add_argument('--num-words', type=int, default=2500, help="Word length of blog post.")
    parser.add_argument('-o', '--output-file', default='-', help="Output file.")
    parser_group = parser.add_mutually_exclusive_group(required=True)
    parser_group.add_argument('-q', "--question", type=str, help="Question to ask.")
    parser_group.add_argument('-t', "--topics", type=str, help="Topics to query.")
    args = parser.parse_args()

    start = time.time_ns()
    print('Retrieving documents...', file=sys.stderr)
    retriever = get_retriever(chroma_store_path=args.vector_db,
                              document_db_path=args.document_db)
    documents = rag_fetch(retriever=retriever,
                          llm=args.llm,
                          question=args.question,
                          topics=args.topics,
                          num_chunks=args.num_docs)
    print(f'{len(documents)} chunks retrieved in {(time.time_ns() - start) / 1e9:.1f} seconds.', file=sys.stderr)
    content = query(llm=args.llm, question=args.question, topics=args.topics, output_format=args.output_format,
                    context=documents, num_slides=args.num_slides, num_words=args.num_words)
    if not args.output_file or args.output_file == '-':
        print(content)
    else:
        with open(args.output_file, 'w') as f:
            f.write(content)
    end = time.time_ns()
    print(f"\nFinished content generation in {(end - start) / 1e9:.1f} seconds.", file=sys.stderr)


if __name__ == "__main__":
    _main()

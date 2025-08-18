from langchain.chains import LLMChain
from langchain.chains.summarize import load_summarize_chain
from langchain.prompts import PromptTemplate
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_ollama import ChatOllama
from langchain_text_splitters import RecursiveCharacterTextSplitter

llm = ChatOllama(model="gemma3:12b")
title_prompt = PromptTemplate.from_template("Extract a title for this OGC (Open Geospatial Consortium) document. "
                                            "Just return the best title "
                                            "you can think of, nothing else but the title.\n\n{content}")
title_chain = title_prompt | llm | StrOutputParser()

summary_prompt = PromptTemplate.from_template('Summarize the following document. Do not include any'
                                              ' introductory text, just return the summary:\n\n{content}')
summary_chain = (
        summary_prompt
        | llm
        | StrOutputParser()
)


def extract_title(content: str):
    return title_chain.invoke({"content": content[:15000]}).strip()


class Summarizer:

    def __init__(self, model: str = 'gemma3:12b', chain_type = 'map_reduce'):
        self.chain = load_summarize_chain(ChatOllama(model=model), chain_type=chain_type)
        self.splitter = RecursiveCharacterTextSplitter(chunk_size=10_000, chunk_overlap=200)

    def summarize(self, content: str | Document):
        document = content if isinstance(content, Document) else Document(page_content=content)
        return self.chain.invoke(self.splitter.split_documents([document]))['output_text']

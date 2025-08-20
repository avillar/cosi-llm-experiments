from langchain.chains import LLMChain
from langchain.chains.summarize import load_summarize_chain
from langchain.prompts import PromptTemplate
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableLambda
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter

chat_models = {
    'ollama': ChatOllama,
    'openai': ChatOpenAI,
}


def get_llm(provider: str, model: str, **kwargs):
    llm = chat_models.get(provider)
    if not llm:
        raise ValueError(f'Unknown provider: {provider}')
    return llm(model=model, **kwargs)


title_prompt = PromptTemplate.from_template(
    "Extract a title for this OGC (Open Geospatial Consortium) document. "
    "Just return the best title "
    "you can think of, nothing else but the title.\n\n{content}"
)

keyword_prompt = PromptTemplate.from_template(
    "Extract the absolute top 10 keywords from this document. Include "
    "any potential acronyms and project names that could look like "
    "common words. Only return a list of comma-separated keywords, "
    "do not include any notes or comments:\n\n{content}"
)


class Summarizer:

    def __init__(self, model: str = 'gemma3:12b', llm_provider='ollama', chain_type='map_reduce', **kwargs):
        self.chat_model = get_llm(llm_provider, model=model, temperature=.3)
        self.chain = load_summarize_chain(self.chat_model, chain_type=chain_type, **kwargs)
        self.splitter = RecursiveCharacterTextSplitter(chunk_size=10_000, chunk_overlap=200)
        self.title_chain = title_prompt | self.chat_model | StrOutputParser()
        self.keyword_chain = keyword_prompt | self.chat_model | StrOutputParser()

    def extract_title(self, content: str):
        return self.title_chain.invoke({"content": content[:3000]}).strip()

    def summarize(self, content: str | Document):
        document = content if isinstance(content, Document) else Document(page_content=content)
        return self.chain.invoke(self.splitter.split_documents([document]))['output_text']

    def extract_keywords(self, content: str) -> set[str]:
        return {k.strip() for k in self.keyword_chain.invoke({"content": content}).split(',') if k}

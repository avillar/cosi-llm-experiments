from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_ollama import ChatOllama

llm = ChatOllama(model="gemma3:12b")
title_prompt = PromptTemplate.from_template("Extract a title for this OGC (Open Geospatial Consortium) document. "
                                            "Just return the best title "
                                            "you can think of, nothing else but the title.\n\n{content}")
title_chain = title_prompt | llm | StrOutputParser()

summary_prompt = PromptTemplate.from_template('Summarize the following document:\n\n{content}')
summary_chain = (
        summary_prompt
        | llm
        | StrOutputParser()
)


def extract_title(content: str):
    return title_chain.invoke({"content": content[:15000]}).strip()


class Summarizer:

    def __init__(self, model: str = 'gemma3:12b'):
        self.chain = (
                summary_prompt
                | ChatOllama(model=model)
                | StrOutputParser()
        )

    def summarize(self, content: str):
        return self.chain.invoke({"content": content}).strip()

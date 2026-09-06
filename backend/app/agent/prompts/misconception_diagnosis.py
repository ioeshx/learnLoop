"""Prompt for diagnosing an error before bounded remediation."""

from pydantic import Field

from app.agent.prompts.models import PromptInput, PromptTemplate
from app.agent.schemas import MisconceptionDiagnosis


class MisconceptionDiagnosisInput(PromptInput):
    knowledge_node: str = Field(min_length=1)
    question: str = Field(min_length=1)
    expected_answer: list[str] = Field(min_length=1)
    learner_answer: list[str] = Field(min_length=1)


MISCONCEPTION_DIAGNOSIS_PROMPT = PromptTemplate(
    name="misconception_diagnosis",
    version="1.0.0",
    use_case="Diagnose one observed wrong answer before bounded remediation.",
    input_schema=MisconceptionDiagnosisInput,
    output_schema=MisconceptionDiagnosis,
    system_template="""
你是谨慎的错因诊断器。只能依据题目、标准答案和学习者本次答案推断可能的误区。
不得编造学习者背景；证据必须来自本次作答。仅输出符合指定 Schema 的 JSON。
""",
    user_template="""
知识点：{knowledge_node}
题目：{question}
标准答案：{expected_answer}
学习者答案：{learner_answer}
""",
    test_input={
        "knowledge_node": "广度优先搜索",
        "question": "BFS 使用哪种数据结构？",
        "expected_answer": ["队列"],
        "learner_answer": ["栈"],
    },
)

"""Prompt for evaluating a future non-objective answer."""

from pydantic import Field

from app.agent.prompts.models import PromptInput, PromptTemplate
from app.agent.schemas import AnswerEvaluation


class AnswerEvaluationInput(PromptInput):
    question: str = Field(min_length=1)
    rubric: str = Field(min_length=1)
    reference_answer: str = Field(min_length=1)
    learner_answer: str = Field(min_length=1)


ANSWER_EVALUATION_PROMPT = PromptTemplate(
    name="answer_evaluation",
    version="1.0.0",
    use_case="Evaluate a short answer against an explicit rubric.",
    input_schema=AnswerEvaluationInput,
    output_schema=AnswerEvaluation,
    system_template="""
你是严格且可解释的简答题评测器。只能依据题目、评分规则和参考答案评分。
反馈必须指出答案中的具体证据，不推断学习者身份。仅输出符合指定 Schema 的 JSON。
""",
    user_template="""
题目：{question}
评分规则：{rubric}
参考答案：{reference_answer}
学习者答案：{learner_answer}
""",
    test_input={
        "question": "BFS 为什么使用队列？",
        "rubric": "说明先进先出与逐层遍历的关系",
        "reference_answer": "队列保证节点按发现顺序展开，因此逐层遍历。",
        "learner_answer": "队列让先发现的节点先展开。",
    },
)

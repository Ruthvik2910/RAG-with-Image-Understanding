from typing import Any, Dict

from backend.graph.chains.generation import generation_chain
from backend.graph.context import format_documents
from backend.graph.state import GraphState


def generate(state: GraphState) -> Dict[str, Any]:
    print("---GENERATE---")

    question = state["question"]

    documents = state.get(
        "documents",
        [],
    )

    context = format_documents(
        documents
    )

    image_analysis = state.get("image_analysis", "").strip()
    if image_analysis:
        image_context = (
            "[Uploaded Image Analysis / OCR]\n"
            + image_analysis
        )
        context = (
            context + "\n\n" + image_context
            if context
            else image_context
        )

    generation = generation_chain.invoke(
        {
            "context": context,
            "question": question,
        }
    )

    return {
        "generation": generation,
        "question": question,
    }
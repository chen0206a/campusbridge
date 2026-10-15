"""Serve with optional embeddings warmed before request/ContextAgent time budgets."""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sqlalchemy import select
from campusbridge.api import create_app
from campusbridge.db import KnowledgeDocument
from campusbridge.dense_retrieval import RetrievalUnavailable, SemanticKnowledge
from campusbridge.settings import Settings


def warm(app):
    knowledge = app.state.knowledge
    from campusbridge.retrieval_cache import CachedKnowledge
    if isinstance(knowledge, CachedKnowledge):
        knowledge = knowledge.inner
    if not isinstance(knowledge, SemanticKnowledge):
        return []
    with knowledge.db.sessions() as session:
        campuses = list(session.scalars(select(KnowledgeDocument.campus_id).where(KnowledgeDocument.active.is_(True)).distinct()))
    results = []
    for campus in campuses:
        try:
            results.append({"campus_id": campus, **knowledge.build_index(campus)})
        except RetrievalUnavailable:
            if knowledge.config.fallback != "bm25":
                raise
            logging.getLogger(__name__).warning("DENSE_STARTUP_UNAVAILABLE_EXPLICIT_BM25_FALLBACK")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8014)
    args = parser.parse_args()
    app = create_app(Settings.from_environment())
    try:
        warm(app)
    except BaseException:
        app.state.database.close()
        raise
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__": main()

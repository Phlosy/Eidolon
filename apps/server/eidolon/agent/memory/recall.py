"""Recall: what the context builder injects (§4.4 "memory: procedural → semantic →
episodic, 各有 token 上限"). Returns ready-to-paste sections; empty when nothing
matches, so a run without memory produces the same request as before."""

from dataclasses import dataclass

from sqlalchemy.orm import sessionmaker

from eidolon.agent.memory.repository import Hit, MemoryRepository, Viewer


@dataclass
class MemoryRecall:
    session_factory: sessionmaker
    repository: MemoryRepository
    semantic_limit: int = 5
    episodic_limit: int = 3
    max_chars: int = 2400

    def recall(self, viewer: Viewer, query: str) -> tuple[list[Hit], list[Hit]]:
        with self.session_factory() as db:
            semantic = self.repository.search(
                db, viewer, query, layers=("semantic",), limit=self.semantic_limit
            )
            episodic = self.repository.search(
                db, viewer, query, layers=("episodic",), limit=self.episodic_limit
            )
        return semantic, episodic

    def sections(self, viewer: Viewer, query: str) -> list[str]:
        semantic, episodic = self.recall(viewer, query)
        out: list[str] = []
        budget = self.max_chars
        if semantic:
            block = "Relevant knowledge:\n" + "\n".join(_line(h) for h in semantic)
            out.append(block[:budget])
            budget -= len(out[-1])
        if episodic and budget > 200:
            block = "From your past work:\n" + "\n".join(_line(h) for h in episodic)
            out.append(block[:budget])
        return out


def _line(hit: Hit) -> str:
    body = " ".join(hit.body.split())
    return f"- [{hit.scope}] {hit.title}: {body[:400]}"

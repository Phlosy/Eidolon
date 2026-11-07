"""Talent (R5-a): evidence, competency, resume, profile — all projections or
queries over facts the other layers record. Nothing below reads this layer, so a
competency can never become an input to selection (contract T1)."""

from eidolon.talent.competency import ALPHA, Competency
from eidolon.talent.evidence import SIGNALS, SOURCE_KINDS, Evidence, EvidenceSource
from eidolon.talent.profile import Profile, ResumeEntry, profile, resume
from eidolon.talent.projection import (
    CompetencyRow,
    EvidenceRow,
    TalentProjection,
    competencies_of,
    evidence_of,
)

__all__ = [
    "ALPHA",
    "SIGNALS",
    "SOURCE_KINDS",
    "Competency",
    "CompetencyRow",
    "Evidence",
    "EvidenceRow",
    "EvidenceSource",
    "Profile",
    "ResumeEntry",
    "TalentProjection",
    "competencies_of",
    "evidence_of",
    "profile",
    "resume",
]

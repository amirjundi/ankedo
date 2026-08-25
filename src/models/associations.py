"""Association tables linking lexicon entries and tropes to target groups.

Many-to-many because hate speech in this domain is not one-term-one-group:

* some slurs are specific to a single community
* some framing (takfiri accusations, impurity/contamination language) is applied
  almost identically to Yazidis, Christians, Mandaeans and Baha'i
* some abuse is generic and carries no group reference at all

Scope is stated explicitly on the entry rather than inferred from an empty link
table — an accidentally-empty set silently becoming "applies to everyone" is the
over-flagging failure this design exists to prevent.
"""
from __future__ import annotations

from sqlalchemy import Column, ForeignKey, String, Table

from src.models.base import Base

lexicon_target_groups = Table(
    "lexicon_target_groups",
    Base.metadata,
    Column("lexicon_entry_id", String(36), ForeignKey("lexicon_entries.id"), primary_key=True),
    Column("target_group_id", String(36), ForeignKey("target_groups.id"), primary_key=True),
)

trope_target_groups = Table(
    "trope_target_groups",
    Base.metadata,
    Column("trope_entry_id", String(36), ForeignKey("trope_entries.id"), primary_key=True),
    Column("target_group_id", String(36), ForeignKey("target_groups.id"), primary_key=True),
)

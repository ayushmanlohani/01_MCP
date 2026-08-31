"""Flat, tag-filterable notes backed by MongoDB. Content is append-only bullets."""

import os
from datetime import datetime, timezone

from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

# Must be typing_extensions, not typing: on Python < 3.12 pydantic refuses to build
# a schema from a typing.TypedDict, which breaks the SDK's output-schema generation.
from typing_extensions import TypedDict

DB_NAME = "learning_mcp"
COLLECTION_NAME = "notes"

_client: MongoClient | None = None


class ContentEntry(TypedDict):
    """One bullet in a note."""

    text: str
    added_at: str


class Note(TypedDict):
    """A note, in full."""

    name: str
    tags: list[str]
    content: list[ContentEntry]
    created_at: str
    updated_at: str


class NoteSummary(TypedDict):
    """A note without its bullets, for browsing/filtering."""

    name: str
    tags: list[str]
    updated_at: str
    bullet_count: int


class CreateNoteResponse(TypedDict):
    """The created note, plus a tag hint when none were given."""

    note: Note
    hint: str | None


class UpdateNoteResponse(TypedDict):
    """The updated note, plus how many bullets the delete step removed."""

    note: Note
    deleted_count: int


def _get_collection():
    """Return the `notes` collection, connecting and indexing on first use."""
    global _client
    if _client is None:
        uri = os.getenv("MONGO_URI")
        if not uri:
            raise RuntimeError("MONGO_URI is not set")
        _client = MongoClient(uri)
        collection = _client[DB_NAME][COLLECTION_NAME]
        collection.create_index("name_lower", unique=True)
        collection.create_index("tags")
    return _client[DB_NAME][COLLECTION_NAME]


def _to_note(doc: dict) -> Note:
    return Note(
        name=doc["name"],
        tags=doc["tags"],
        content=[
            ContentEntry(text=entry["text"], added_at=entry["added_at"].isoformat())
            for entry in doc["content"]
        ],
        created_at=doc["created_at"].isoformat(),
        updated_at=doc["updated_at"].isoformat(),
    )


def create_note(
    name: str, content: list[str], tags: list[str] | None = None
) -> CreateNoteResponse:
    """Create a new note.

    Args:
        name: Display name, e.g. "Groceries". Must be unique (case-insensitive).
        content: Initial bullets.
        tags: Optional tags, normalized to lowercase.

    Returns:
        The created note, plus `hint`: set to a list of existing tags when
        `tags` was empty, so a near-duplicate tag isn't created by accident.

    Raises:
        ValueError: `name` is blank, or a note with that name already exists.
    """
    if not name.strip():
        raise ValueError("name must not be blank")

    tags_lower = sorted({t.strip().lower() for t in (tags or []) if t.strip()})
    now = datetime.now(timezone.utc)

    doc = {
        "name": name.strip(),
        "name_lower": name.strip().lower(),
        "content": [{"text": text, "added_at": now} for text in content],
        "tags": tags_lower,
        "created_at": now,
        "updated_at": now,
    }

    collection = _get_collection()
    try:
        collection.insert_one(doc)
    except DuplicateKeyError as exc:
        raise ValueError(f"a note named '{name}' already exists") from exc

    hint = None
    if not tags_lower:
        existing_tags = sorted(t for t in collection.distinct("tags") if t)
        if existing_tags:
            hint = f"Existing tags you could reuse: {', '.join(existing_tags)}"

    return CreateNoteResponse(note=_to_note(doc), hint=hint)


def update_note(
    name: str,
    add_text: list[str] | None = None,
    delete_text: list[str] | None = None,
) -> UpdateNoteResponse:
    """Delete matching bullets, then append new ones, in a single call.

    Args:
        name: The note to update. Matched case-insensitively.
        add_text: New bullets to append.
        delete_text: Bullets to remove — every bullet whose text exactly
            matches any string in this list is deleted, including duplicates.

    Returns:
        The updated note, plus `deleted_count`.

    Raises:
        ValueError: No note named `name` exists.
    """
    name_lower = name.strip().lower()
    collection = _get_collection()
    existing = collection.find_one({"name_lower": name_lower})
    if existing is None:
        raise ValueError(f"no note named '{name}' found")

    deleted_count = 0
    if delete_text:
        deleted_count = sum(1 for entry in existing["content"] if entry["text"] in delete_text)
        collection.update_one(
            {"name_lower": name_lower},
            {"$pull": {"content": {"text": {"$in": delete_text}}}},
        )

    now = datetime.now(timezone.utc)
    update: dict = {"$set": {"updated_at": now}}
    if add_text:
        update["$push"] = {
            "content": {"$each": [{"text": text, "added_at": now} for text in add_text]}
        }
    collection.update_one({"name_lower": name_lower}, update)

    updated = collection.find_one({"name_lower": name_lower})
    return UpdateNoteResponse(note=_to_note(updated), deleted_count=deleted_count)


def get_note(name: str) -> Note:
    """Return a note in full, including every bullet.

    Raises:
        ValueError: No note named `name` exists.
    """
    doc = _get_collection().find_one({"name_lower": name.strip().lower()})
    if doc is None:
        raise ValueError(f"no note named '{name}' found")
    return _to_note(doc)


def list_notes(tag: str | None = None) -> list[NoteSummary]:
    """List notes as summaries (no bullet content), optionally filtered by tag.

    Args:
        tag: If given, only notes carrying this tag (case-insensitive) are
            returned.

    Returns:
        Matching notes as `{name, tags, updated_at, bullet_count}`. Empty list
        if nothing matches — not an error.
    """
    query = {"tags": tag.strip().lower()} if tag else {}
    docs = _get_collection().find(query)
    return [
        NoteSummary(
            name=doc["name"],
            tags=doc["tags"],
            updated_at=doc["updated_at"].isoformat(),
            bullet_count=len(doc["content"]),
        )
        for doc in docs
    ]


def delete_note(name: str) -> None:
    """Hard delete a note.

    Raises:
        ValueError: No note named `name` exists.
    """
    result = _get_collection().delete_one({"name_lower": name.strip().lower()})
    if result.deleted_count == 0:
        raise ValueError(f"no note named '{name}' found")

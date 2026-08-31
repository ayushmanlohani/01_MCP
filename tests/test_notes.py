import copy
from unittest.mock import MagicMock, patch

import pytest
from pymongo.errors import DuplicateKeyError

import tools.notes as notes_module
from tools.notes import create_note, delete_note, get_note, list_notes, update_note


class FakeCollection:
    """A tiny in-memory stand-in for a pymongo Collection.

    Implements just the query shapes notes.py actually issues ($pull/$push/
    $each/$set, $in, exact-match, list membership) so tests exercise real
    query semantics instead of just recording mock calls.
    """

    def __init__(self):
        self.docs = []

    def create_index(self, *args, **kwargs):
        pass

    def insert_one(self, doc):
        if any(d["name_lower"] == doc["name_lower"] for d in self.docs):
            raise DuplicateKeyError("duplicate name_lower")
        self.docs.append(doc)

    def find_one(self, query):
        for doc in self.docs:
            if self._matches(doc, query):
                return copy.deepcopy(doc)
        return None

    def find(self, query=None):
        query = query or {}
        return [copy.deepcopy(doc) for doc in self.docs if self._matches(doc, query)]

    def distinct(self, field):
        # Real MongoDB quirk: distinct on an array field contributes None for
        # any document where that array is empty, instead of contributing
        # nothing. Replicated here since it broke create_note's hint logic.
        values = set()
        for doc in self.docs:
            value = doc.get(field)
            if isinstance(value, list):
                values.update(value or [None])
            elif value is not None:
                values.add(value)
        return list(values)

    def delete_one(self, query):
        for i, doc in enumerate(self.docs):
            if self._matches(doc, query):
                del self.docs[i]
                return MagicMock(deleted_count=1)
        return MagicMock(deleted_count=0)

    def update_one(self, query, update):
        for doc in self.docs:
            if not self._matches(doc, query):
                continue
            if "$pull" in update:
                for field, condition in update["$pull"].items():
                    doc[field] = [item for item in doc[field] if not self._matches(item, condition)]
            if "$push" in update:
                for field, value in update["$push"].items():
                    if isinstance(value, dict) and "$each" in value:
                        doc[field].extend(value["$each"])
                    else:
                        doc[field].append(value)
            if "$set" in update:
                doc.update(update["$set"])
            return

    @staticmethod
    def _matches(doc, query):
        for key, expected in query.items():
            actual = doc.get(key)
            if isinstance(expected, dict) and "$in" in expected:
                if actual not in expected["$in"]:
                    return False
            elif isinstance(actual, list):
                if expected not in actual:
                    return False
            elif actual != expected:
                return False
        return True


class FakeMongoClient:
    """Stands in for pymongo.MongoClient: any db/collection name resolves to
    the same shared FakeCollection, matching how notes.py always addresses
    one fixed db/collection.
    """

    def __init__(self, *args, **kwargs):
        self._collection = FakeCollection()

    def __getitem__(self, _db_name):
        collection = self._collection
        return type("FakeDB", (), {"__getitem__": lambda self, _collection_name: collection})()


@pytest.fixture(autouse=True)
def fake_mongo(monkeypatch):
    monkeypatch.setenv("MONGO_URI", "mongodb://fake")
    notes_module._client = None
    with patch("tools.notes.MongoClient", FakeMongoClient):
        yield
    notes_module._client = None


def test_create_note_with_no_tags_hints_at_existing_tags():
    create_note("Groceries", ["milk"], tags=["shopping"])

    response = create_note("Todo", ["walk dog"])

    assert response["note"]["name"] == "Todo"
    assert response["note"]["tags"] == []
    assert response["hint"] == "Existing tags you could reuse: shopping"


def test_create_note_hint_ignores_notes_with_no_tags():
    create_note("Todo", ["walk dog"])  # no tags -> tags: []

    response = create_note("Groceries", ["milk"])

    assert response["hint"] is None


def test_create_note_with_tags_gets_no_hint():
    response = create_note("Groceries", ["milk"], tags=["Shopping"])

    assert response["hint"] is None
    assert response["note"]["tags"] == ["shopping"]


def test_create_note_normalizes_and_stores_content_with_timestamps():
    response = create_note("Groceries", ["milk", "eggs"])

    bullets = response["note"]["content"]
    assert [b["text"] for b in bullets] == ["milk", "eggs"]
    assert all(b["added_at"] for b in bullets)


def test_duplicate_name_is_rejected_case_insensitively():
    create_note("Groceries", ["milk"])

    with pytest.raises(ValueError):
        create_note("groceries", ["eggs"])


def test_update_note_appends_bullets():
    create_note("Groceries", ["milk"])

    response = update_note("Groceries", add_text=["eggs", "bread"])

    assert [b["text"] for b in response["note"]["content"]] == ["milk", "eggs", "bread"]
    assert response["deleted_count"] == 0


def test_update_note_deletes_all_exact_matches():
    create_note("Groceries", ["milk", "milk", "eggs"])

    response = update_note("Groceries", delete_text=["milk"])

    assert [b["text"] for b in response["note"]["content"]] == ["eggs"]
    assert response["deleted_count"] == 2


def test_update_note_add_and_delete_in_one_call():
    create_note("Groceries", ["milk"])

    response = update_note("groceries", add_text=["bread"], delete_text=["milk"])

    assert [b["text"] for b in response["note"]["content"]] == ["bread"]
    assert response["deleted_count"] == 1


def test_update_note_missing_raises():
    with pytest.raises(ValueError):
        update_note("Nonexistent", add_text=["x"])


def test_get_note_returns_full_content():
    create_note("Groceries", ["milk"], tags=["shopping"])

    note = get_note("GROCERIES")

    assert note["name"] == "Groceries"
    assert note["tags"] == ["shopping"]
    assert [b["text"] for b in note["content"]] == ["milk"]


def test_get_note_missing_raises():
    with pytest.raises(ValueError):
        get_note("Nonexistent")


def test_list_notes_returns_summaries_not_full_content():
    create_note("Groceries", ["milk", "eggs"], tags=["shopping"])
    create_note("Todo", ["walk dog"])

    summaries = list_notes()

    assert {s["name"] for s in summaries} == {"Groceries", "Todo"}
    groceries = next(s for s in summaries if s["name"] == "Groceries")
    assert groceries["bullet_count"] == 2
    assert "content" not in groceries


def test_list_notes_filters_by_tag_case_insensitively():
    create_note("Groceries", ["milk"], tags=["shopping"])
    create_note("Todo", ["walk dog"])

    summaries = list_notes(tag="Shopping")

    assert [s["name"] for s in summaries] == ["Groceries"]


def test_list_notes_returns_empty_list_when_nothing_matches():
    assert list_notes(tag="nonexistent") == []


def test_delete_note_removes_it():
    create_note("Groceries", ["milk"])

    delete_note("groceries")

    with pytest.raises(ValueError):
        get_note("Groceries")


def test_delete_note_missing_raises():
    with pytest.raises(ValueError):
        delete_note("Nonexistent")

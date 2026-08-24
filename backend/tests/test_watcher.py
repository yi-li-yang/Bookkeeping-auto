"""
Input-folder watcher tests.

The two failure modes that matter: reading a file that is still being written,
and re-ingesting one that is already stored. Both cost real money (the LLM runs
per file) and both corrupt the data rather than erroring cleanly.
"""
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import watcher
from backend.db.database import Base
from backend.db.models import SourceFile


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'w.db'}")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def inbox(tmp_path):
    d = tmp_path / "input"
    d.mkdir()
    return d


def write(path, content="date,amount\n2026-01-01,-5\n"):
    path.write_text(content)
    return path


# -- discovery --------------------------------------------------------------

def test_only_supported_extensions_are_picked_up(inbox):
    write(inbox / "statement.csv")
    write(inbox / "statement.pdf")
    write(inbox / "notes.txt")
    write(inbox / "download.crdownload")

    found = {p.name for p in watcher.candidate_files(inbox)}
    assert found == {"statement.csv", "statement.pdf"}


def test_nested_directories_are_searched(inbox):
    sub = inbox / "amex" / "2026"
    sub.mkdir(parents=True)
    write(sub / "jan.csv")
    assert [p.name for p in watcher.candidate_files(inbox)] == ["jan.csv"]


def test_a_missing_input_directory_is_not_an_error(tmp_path):
    assert watcher.candidate_files(tmp_path / "nope") == []


def test_already_ingested_files_are_not_offered_again(inbox, db):
    write(inbox / "done.csv")
    write(inbox / "new.csv")
    db.add(SourceFile(filename="done.csv", file_type="csv", status="done"))
    db.commit()

    pending = watcher.unprocessed_files(inbox, db)
    assert [p.name for p in pending] == ["new.csv"]


def test_the_lookup_key_is_exactly_what_the_scanner_stores(inbox):
    """
    The watcher and file_scanner must derive SourceFile.filename identically. If
    they disagree on so much as a path separator, every poll sees the file as
    new and re-ingests it -- an LLM call every 20 seconds, forever.
    """
    (inbox / "amex").mkdir()
    path = write(inbox / "amex" / "jan.csv")

    scanner_key = os.path.relpath(str(path), str(inbox))   # file_scanner.py:38
    assert watcher._relative_name(path, inbox) == scanner_key


def test_nested_files_are_matched_by_their_relative_path(inbox, db):
    """
    Two banks can each have a `jan.csv`. Matching on the bare name would treat
    them as one file and silently skip the second.
    """
    (inbox / "amex").mkdir()
    (inbox / "chase").mkdir()
    amex = write(inbox / "amex" / "jan.csv")
    write(inbox / "chase" / "jan.csv")

    db.add(SourceFile(
        filename=watcher._relative_name(amex, inbox), file_type="csv", status="done",
    ))
    db.commit()

    pending = watcher.unprocessed_files(inbox, db)
    assert [p.parent.name for p in pending] == ["chase"]


def test_a_file_that_failed_to_parse_is_not_retried_in_a_loop(inbox, db):
    """
    A deliberate divergence from file_scanner, which retries anything not marked
    'done'. The watcher polls every 20 seconds and each attempt costs an LLM
    call, so a file that cannot be parsed must not be picked up again
    automatically. `POST /api/ingest` still retries it on request.
    """
    write(inbox / "broken.pdf")
    db.add(SourceFile(
        filename="broken.pdf", file_type="pdf", status="error",
        error_message="could not parse",
    ))
    db.commit()

    assert watcher.unprocessed_files(inbox, db) == []


# -- stability --------------------------------------------------------------

def test_a_file_is_not_read_on_first_sight(inbox):
    """
    A browser download is visible from its first byte. Reading it immediately
    parses half a statement into plausible nonsense.
    """
    path = write(inbox / "a.csv")
    tracker = watcher.StabilityTracker()
    assert tracker.settled([path]) == []


def test_a_file_unchanged_across_two_polls_is_ready(inbox):
    path = write(inbox / "a.csv")
    tracker = watcher.StabilityTracker()
    tracker.settled([path])
    assert tracker.settled([path]) == [path]


def test_a_growing_file_keeps_waiting(inbox):
    path = write(inbox / "a.csv", "partial")
    tracker = watcher.StabilityTracker()
    tracker.settled([path])

    path.write_text("partial plus more")     # still downloading
    assert tracker.settled([path]) == []

    assert tracker.settled([path]) == [path]  # settled now


def test_a_vanished_file_is_skipped_not_raised(inbox):
    path = inbox / "gone.csv"
    write(path)
    tracker = watcher.StabilityTracker()
    tracker.settled([path])
    path.unlink()
    assert tracker.settled([path]) == []


def test_the_tracker_does_not_grow_without_bound(inbox):
    """A long-running process must not accumulate an entry per file ever seen."""
    tracker = watcher.StabilityTracker()
    first = write(inbox / "a.csv")
    tracker.settled([first])
    assert len(tracker._seen) == 1

    second = write(inbox / "b.csv")
    tracker.settled([second])                 # `a` no longer offered
    assert list(tracker._seen) == [second]


# -- the poll ---------------------------------------------------------------

def test_check_once_does_nothing_when_the_folder_is_empty(inbox, db, monkeypatch):
    monkeypatch.setattr(watcher, "ingest_now", lambda db: pytest.fail("ingested nothing"))
    w = watcher.InputWatcher(inbox, session_factory=lambda: db)
    assert w.check_once() is None


def test_check_once_waits_for_stability_before_ingesting(inbox, db, monkeypatch):
    calls = []
    monkeypatch.setattr(watcher, "ingest_now", lambda db: calls.append(1) or {"processed": 1})

    write(inbox / "a.csv")
    w = watcher.InputWatcher(inbox, session_factory=lambda: db)

    assert w.check_once() is None       # first sight
    assert calls == []
    assert w.check_once() == {"processed": 1}
    assert calls == [1]


def test_an_ingested_file_is_not_re_ingested(inbox, db, monkeypatch):
    """The scanner records the file, so the next poll must see nothing pending."""
    def fake_ingest(session):
        session.add(SourceFile(filename="a.csv", file_type="csv", status="done"))
        session.commit()
        return {"processed": 1}

    monkeypatch.setattr(watcher, "ingest_now", fake_ingest)

    write(inbox / "a.csv")
    w = watcher.InputWatcher(inbox, session_factory=lambda: db)
    w.check_once()
    assert w.check_once() == {"processed": 1}
    assert w.check_once() is None


# -- the loop ---------------------------------------------------------------

def test_the_loop_polls_and_stops_cleanly(inbox, monkeypatch):
    """The unit tests above cover one poll; this covers the task that drives them."""
    import asyncio

    calls = []

    async def scenario():
        w = watcher.InputWatcher(inbox, poll_seconds=0.01)
        monkeypatch.setattr(w, "check_once", lambda: calls.append(1))
        w.start()
        await asyncio.sleep(0.1)
        await w.stop()
        return w

    w = asyncio.run(scenario())
    assert calls, "the loop never polled"
    assert w._task is None, "the task was not cleaned up"


def test_a_failing_poll_does_not_kill_the_watcher(inbox):
    """One unparseable file must not silently end auto-ingest for the session."""
    import asyncio

    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("bad file")

    async def scenario():
        w = watcher.InputWatcher(inbox, poll_seconds=0.01)
        w.check_once = boom
        w.start()
        await asyncio.sleep(0.1)
        still_running = not w._task.done()
        await w.stop()
        return still_running

    assert asyncio.run(scenario())
    assert len(calls) > 1, "the loop stopped after the first failure"

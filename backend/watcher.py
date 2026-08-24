"""
Watch data/input/ and ingest statements as they appear.

Five of the six bank accounts here have no API an individual can reach, so
their statements are downloaded by hand and always will be. This removes the
step after that: drop the file in, and it is parsed, categorised and stored
without touching the dashboard.

Deliberately a poll, not a filesystem-event library. Polling needs no extra
dependency, behaves identically on every platform and filesystem (watchdog is
unreliable on network and virtual drives), and `scan_and_ingest` already skips
files it has processed, so a redundant poll costs one directory walk.

Note this reverses commit 77c3d25 ("changed ingestion method to manual only"),
which removed a thread that re-ingested on every startup. The difference is
what triggers it: this reacts to a *new file*, never to a restart, and it can
be switched off with AUTO_INGEST=false.
"""
import asyncio
import logging
import os
from pathlib import Path

from sqlalchemy.orm import Session

from backend.db.models import SourceFile
from backend.ingestion.file_scanner import SUPPORTED_EXTENSIONS

logger = logging.getLogger(__name__)

# A file must look identical across two consecutive polls before it is read.
# Browser downloads and copies over a network are visible long before they
# finish, and half a PDF parses into plausible nonsense rather than failing.
_STABILITY_POLLS = 2


def _relative_name(path: Path, input_dir: Path) -> str:
    """Match the key file_scanner stores in SourceFile.filename."""
    return os.path.relpath(str(path), str(input_dir))


def candidate_files(input_dir: Path) -> list[Path]:
    """Every supported file under the input directory, recursively."""
    if not input_dir.is_dir():
        return []
    return sorted(
        p for p in input_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def unprocessed_files(input_dir: Path, db: Session) -> list[Path]:
    """Candidates with no SourceFile row, i.e. genuinely new."""
    files = candidate_files(input_dir)
    if not files:
        return []

    known = {
        row[0] for row in
        db.query(SourceFile.filename)
        .filter(SourceFile.filename.in_([_relative_name(f, input_dir) for f in files]))
        .all()
    }
    return [f for f in files if _relative_name(f, input_dir) not in known]


def _fingerprint(path: Path) -> tuple[int, float] | None:
    try:
        stat = path.stat()
    except OSError:
        return None                     # vanished or locked mid-poll
    return (stat.st_size, stat.st_mtime)


class StabilityTracker:
    """
    Remembers how long each file has looked unchanged.

    Kept as a class rather than a module global so tests -- and a restart --
    start from a clean slate.
    """

    def __init__(self, required_polls: int = _STABILITY_POLLS):
        self._required = required_polls
        self._seen: dict[Path, tuple[tuple[int, float], int]] = {}

    def settled(self, paths: list[Path]) -> list[Path]:
        """Return the paths that have been unchanged for long enough to read."""
        ready: list[Path] = []
        current: dict[Path, tuple[tuple[int, float], int]] = {}

        for path in paths:
            fingerprint = _fingerprint(path)
            if fingerprint is None:
                continue

            previous = self._seen.get(path)
            if previous and previous[0] == fingerprint:
                count = previous[1] + 1
            else:
                count = 1
            current[path] = (fingerprint, count)

            if count >= self._required:
                ready.append(path)

        # Drop files that are gone or already ingested, so the dict cannot grow
        # without bound over a long-running process.
        self._seen = current
        return ready

    def forget(self, paths: list[Path]) -> None:
        for path in paths:
            self._seen.pop(path, None)


def ingest_now(db: Session) -> dict:
    """Run the shared scanner. Separated out so the loop stays trivial."""
    from backend.ingestion.file_scanner import scan_and_ingest

    return scan_and_ingest(db)


class InputWatcher:
    def __init__(self, input_dir: Path, poll_seconds: float = 20.0,
                 session_factory=None):
        self.input_dir = input_dir
        self.poll_seconds = poll_seconds
        self._tracker = StabilityTracker()
        self._task: asyncio.Task | None = None
        self._session_factory = session_factory

    def _session(self) -> Session:
        if self._session_factory is not None:
            return self._session_factory()
        from backend.db.database import SessionLocal

        return SessionLocal()

    def check_once(self) -> dict | None:
        """
        One poll. Returns the ingest result, or None if there was nothing to do.

        Blocking: it walks the directory and may call the LLM. The caller runs
        it off the event loop.
        """
        db = self._session()
        try:
            pending = unprocessed_files(self.input_dir, db)
            if not pending:
                self._tracker.settled([])       # let the tracker forget stale entries
                return None

            ready = self._tracker.settled(pending)
            if not ready:
                logger.debug(
                    "Waiting for %d input file(s) to finish writing.", len(pending)
                )
                return None

            logger.info(
                "Auto-ingesting %d new file(s): %s",
                len(ready), ", ".join(p.name for p in ready),
            )
            result = ingest_now(db)
            self._tracker.forget(ready)
            return result
        finally:
            db.close()

    async def _loop(self) -> None:
        logger.info(
            "Watching %s for new statements (every %.0fs). Set AUTO_INGEST=false "
            "to disable.", self.input_dir, self.poll_seconds,
        )
        while True:
            try:
                await asyncio.sleep(self.poll_seconds)
                result = await asyncio.to_thread(self.check_once)
                if result and result.get("processed"):
                    logger.info(
                        "Auto-ingest complete: %d processed, %d errors.",
                        result["processed"], len(result.get("errors") or []),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:                    # noqa: BLE001
                # One bad file must not stop the watcher for the whole session.
                logger.exception("Auto-ingest poll failed; continuing.")

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

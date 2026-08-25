"""Agent-agnostic attachment intake.

Everything here sits BELOW the agents: Artemis, Callie, Kai and Ares all read
files through this one layer, and any agent added later inherits it without
touching this package. It exists because on 2026-08-25 Josh posted a TSV to the
demand-gen channel and Callie answered around a file she had never been handed --
the upload was discarded at the Slack events boundary before she was ever
invoked.

Two rules shape the design.

**We store readings, not files.** Bytes are fetched, extracted, and dropped. The
source system (Slack, Drive) keeps the original and stays the system of record;
we persist only the extracted text plus a pointer back. Storage therefore grows
with text people actually used, not with every binary anyone drags into a
channel -- and when a file is deleted at the source we are not holding a copy of
it.

**A failure must say which wall it hit.** "I can see it but may not open it" and
"I opened it and could not parse it" are different sentences to the person
waiting on an answer. Every failure in this package carries a `reason` an agent
can repeat verbatim. Silence is what made the original bug invisible.
"""

from artemis.files.extract.base import (
    AccessDeniedError,
    ExtractedFile,
    FileParseError,
    FileTooLargeError,
    UnsupportedFileTypeError,
)

__all__ = [
    "AccessDeniedError",
    "ExtractedFile",
    "FileParseError",
    "FileTooLargeError",
    "UnsupportedFileTypeError",
]

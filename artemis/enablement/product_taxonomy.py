"""Amira product taxonomy — the single source of truth Kai uses to translate how
people *talk* about products into the names assets are actually *filed* under.

The gap this closes (found 2026-06-25, Sara's "Lectura ILP video" question):
people name a **suite/language + a function** ("a video of a Lectura ILP lesson"),
but the asset is filed under the **specific product name** ("Enseñar"). The word
"Lectura" appears nowhere on the Enseñar video, so neither vector nor keyword search
connected them and Kai truthfully reported "no ILP student video." Searching the
literal product name ("Enseñar ILP video") returns it as the #1 result.

This module feeds TWO consumers from one definition so they can never drift:
  1. ``expand_query`` — appends the canonical product name to a search query when a
     suite + function are both named, so retrieval connects regardless of phrasing.
  2. ``glossary_text`` — renders the product cheat-sheet baked into Kai's persona, so
     he *reasons* about products correctly (and re-queries with the right name).

The Amira Dual Language Suite (all 6 products):
  Amira Reading Suite  (English):  ISIP Assess | Instruct | Tutor
  Amira Lectura        (Spanish):  ISIP Evaluar | Tutora  | Enseñar
Parallel by function — Assessment (ISIP), Instruction (ILP), Tutoring.
("The Story of America" is a separate, standalone product, outside the suite.)
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

# Suite ("english" = Amira Reading Suite, "spanish" = Amira Lectura).
ENGLISH = "english"
SPANISH = "spanish"

# Function within a suite.
ASSESSMENT = "assessment"
INSTRUCTION = "instruction"
TUTORING = "tutoring"


@dataclass(frozen=True)
class Product:
    canonical: str  # the name assets are filed under
    suite: str
    function: str
    aliases: tuple[str, ...] = field(default_factory=tuple)


# The 6 Dual Language Suite products. Aliases are extra spellings people type.
PRODUCTS: tuple[Product, ...] = (
    Product("Amira ISIP Assess", ENGLISH, ASSESSMENT, ("isip assess",)),
    Product("Amira Instruct", ENGLISH, INSTRUCTION, ()),
    Product("Amira Tutor", ENGLISH, TUTORING, ()),
    Product("Amira ISIP Evaluar", SPANISH, ASSESSMENT, ("isip evaluar", "evaluar")),
    Product("Amira Enseñar", SPANISH, INSTRUCTION, ("ensenar",)),
    Product("Amira Tutora", SPANISH, TUTORING, ()),
)

# Words that signal a suite / language context.
_SUITE_ALIASES: dict[str, tuple[str, ...]] = {
    ENGLISH: ("reading suite", "english"),
    SPANISH: ("lectura", "spanish", "espanol"),
}

# Words that signal a function. "ilp" / "individualized learning" = instruction.
_FUNCTION_ALIASES: dict[str, tuple[str, ...]] = {
    ASSESSMENT: ("isip", "assess", "evaluar", "assessment", "diagnostic", "screener"),
    INSTRUCTION: ("ilp", "individualized learning", "instruct", "instruction", "ensenar"),
    TUTORING: ("tutoring", "tutora", "tutor"),
}

_SUITE_LABEL = {ENGLISH: "Amira Reading Suite (English)", SPANISH: "Amira Lectura (Spanish)"}
_FUNCTION_LABEL = {
    ASSESSMENT: "Assessment (ISIP)",
    INSTRUCTION: "Instruction (ILP)",
    TUTORING: "Tutoring",
}


def _norm(text: str) -> str:
    """Lowercase and strip accents so 'Enseñar' matches 'ensenar'."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _product_for(suite: str, function: str) -> Product | None:
    for p in PRODUCTS:
        if p.suite == suite and p.function == function:
            return p
    return None


def expand_query(query: str) -> str:
    """Append the canonical product name when a query names a suite + a function.

    Conservative on purpose: expansion only fires when BOTH a language/suite cue AND
    a function cue are present, because that is exactly when the specific product is
    unambiguous. "Lectura ILP" -> + "Amira Enseñar"; "ILP" alone (no language) ->
    unchanged, so broad queries stay broad. The appended name flows into the
    embedding and the keyword re-rank, which is what surfaces the right asset.
    """
    if not query or not query.strip():
        return query
    q = _norm(query)

    suites = {s for s, aliases in _SUITE_ALIASES.items() if any(a in q for a in aliases)}
    funcs = {f for f, aliases in _FUNCTION_ALIASES.items() if any(a in q for a in aliases)}

    additions: list[str] = []
    for suite in suites:
        for function in funcs:
            product = _product_for(suite, function)
            if product is None:
                continue
            for token in (product.canonical, *product.aliases):
                if _norm(token) not in q and token not in additions:
                    additions.append(token)

    if not additions:
        return query
    return f"{query} {' '.join(additions)}"


def glossary_text() -> str:
    """Render the product cheat-sheet for Kai's persona (single source of truth)."""
    rows = []
    for function in (ASSESSMENT, INSTRUCTION, TUTORING):
        eng = _product_for(ENGLISH, function)
        spa = _product_for(SPANISH, function)
        rows.append(
            f"  - {_FUNCTION_LABEL[function]}: "
            f"English = {eng.canonical if eng else '?'}, "
            f"Spanish = {spa.canonical if spa else '?'}"
        )
    table = "\n".join(rows)
    return f"""## Amira product map (know this cold — assets are filed by product name)

Amira Dual Language Suite = all 6 products. Two suites, parallel by function:
  - {_SUITE_LABEL[ENGLISH]}: ISIP Assess, Instruct, Tutor
  - {_SUITE_LABEL[SPANISH]}: ISIP Evaluar, Tutora, Enseñar

Same function, different name per language:
{table}

Key translations (people name a suite + a function; the asset is filed under the product):
  - "ILP" = Individualized Learning Pathways = the INSTRUCTION product.
  - "Lectura ILP" / "Spanish ILP / instruction" -> the asset is Enseñar. Search "Enseñar".
  - "English ILP" / "Reading Suite instruction" -> the asset is Instruct. Search "Instruct".
  - "Lectura assessment / ISIP" -> ISIP Evaluar. "Lectura tutoring" -> Tutora.
So when someone asks for a Lectura/Spanish ILP lesson or video, search the product name
(Enseñar) too — the asset will not contain the word "Lectura". Never tell someone an asset
does not exist before you have searched the specific product name it would be filed under.

"The Story of America" is a separate standalone product, outside the Dual Language Suite."""

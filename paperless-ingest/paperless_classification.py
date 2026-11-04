"""
paperless_classification.py

How documents are classified in Paperless — step 7 of the Paperless plan in home-infra-manual.md (decisions 12
and 13, settled 2026-11-04). Read by paperless_setup.py, which creates all of this in Paperless, and by the
backfill (step 8).

Three kinds of rules, applied in this order — a later one overrides an earlier one's correspondent and document
type, while tags add up:

  1. FOLDER_RULES     — backfill only. The archive folders a file sits in, each matched in full.
  2. BACKFILL_NAME_RULES — backfill only. Filename patterns for historical names that will not recur.
  3. WORKFLOWS        — Paperless workflows, for names that keep coming. Paperless applies them to every new
                        document, the backfill's uploads included, so they override what the backfill sends.

Anything no rule covers is left to Paperless's automatic matching (Auto), which learns from the backfill.

Patterns:
  - FOLDER_RULES and BACKFILL_NAME_RULES are Python regular expressions; folder patterns must match the whole
    folder name, name patterns are searched in the filename (case-insensitive).
  - WORKFLOWS use Paperless's own filename filter: a shell-style pattern (* and ?) that must match the whole
    filename, case-insensitive.
  - Names are compared in NFC form ("å" as one character), as the upload scripts send them.
"""

from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass(frozen=True)
class Assign:
    correspondent: Optional[str] = None
    document_type: Optional[str] = None
    tags: Tuple[str, ...] = field(default_factory=tuple)


# ── What exists in Paperless ──────────────────────────────────────────────────

CORRESPONDENTS = (
    # Banks, pensions, insurance
    "Nordea", "SEB", "SBAB", "SPP", "Skandia", "Avanza", "Collectum", "Pensionsmyndigheten",
    "Länsförsäkringar", "Trygg Hansa", "IF",
    # Authorities
    "Skatteverket", "Försäkringskassan",
    # Bills and shops
    "Ellevio", "Skellefteå Kraft", "Coop", "ICA", "SAS EuroBonus", "Apple", "Volvo", "Hertz", "Telia", "SATS",
    "CityOptik", "Securitas", "Bromma Flygfält", "Unoform",
    # Work
    "Pricer", "Convini", "Microsoft", "AWS",
)

DOCUMENT_TYPES = (
    "Årsbesked", "Deklaration", "Lönebesked", "Faktura", "Betalning", "Kvartalsbesked", "Kvitto", "Avtal",
    "Försäkring", "Intyg", "Aktienota",
)

TAGS = (
    "Husbygge", "Semester", "Inbrott", "Bil", "Hälsa", "Pension",
    # People other than the owner, who is the default and has no tag
    "Max", "Tilde", "Helen", "Folke",
)

# Marks everything the backfill imports. Never assigned automatically.
BACKFILL_TAG = "Backfill"


# ── 1. Folders (backfill only) ────────────────────────────────────────────────
# A file gets the rules of every folder in its path. Folders not listed — the year folders, "2019 och tidigare",
# "Införda", "Before sort" … — add nothing. So do the mixed folders "2010 - Årsbesked och Kök",
# "2011 - Årsbesked", "2012 - Årsbesked" and "2013 - Årsbesked och inbrott": their loose files are not
# årsbesked, while their subfolders "Årsbesked" and "Inbrott" are listed below.

FOLDER_RULES = (
    # The "Årsbesked" folders hold the year's tax and finance papers: mostly årsbesked, but also deklarationer
    # (made Deklaration by a workflow below) and a few receipts.
    (r"Årsbesked", Assign(document_type="Årsbesked")),
    (r"Årsbesked 2016", Assign(document_type="Årsbesked")),
    (r"\d{4} Årsbesked", Assign(document_type="Årsbesked")),   # 1991 Årsbesked … 2009 Årsbesked

    (r"Nordea", Assign(correspondent="Nordea")),
    (r"SEB", Assign(correspondent="SEB")),
    (r"Skandia", Assign(correspondent="Skandia")),
    (r"Länsförsäkringar", Assign(correspondent="Länsförsäkringar")),
    (r"Trygg [Hh]ansa", Assign(correspondent="Trygg Hansa")),
    (r"Telia", Assign(correspondent="Telia")),
    (r"SATS", Assign(correspondent="SATS")),
    (r"AWS", Assign(correspondent="AWS")),
    (r"COOP", Assign(correspondent="Coop")),
    (r"CityOptik - Glasögon", Assign(correspondent="CityOptik", tags=("Hälsa",))),
    (r"Lön", Assign(document_type="Lönebesked")),
    (r"Lön Pricer", Assign(correspondent="Pricer", document_type="Lönebesked")),
    (r"Kontrakt", Assign(document_type="Avtal")),
    (r"Kvitton", Assign(document_type="Kvitto")),
    (r"AktieNota", Assign(document_type="Aktienota")),
    (r"Kursintyg", Assign(document_type="Intyg")),
    (r"Bilprovning", Assign(tags=("Bil",))),
    (r"Husbygge", Assign(tags=("Husbygge",))),
    (r"Semester", Assign(tags=("Semester",))),
    (r"Florida", Assign(tags=("Semester",))),
    (r"Inbrott", Assign(tags=("Inbrott",))),
    (r"Inskickade sjukvårdsförskr", Assign(correspondent="Länsförsäkringar", tags=("Hälsa",))),
)


# ── 2. Historical filenames (backfill only) ───────────────────────────────────

BACKFILL_NAME_RULES = (
    # Spelling variants and typos of names the workflows cover
    (r"^(Läsförsäkringar|Länsförsökringar|Länsförskr)", Assign(correspondent="Länsförsäkringar")),
    (r"- Länsförskr -", Assign(correspondent="Länsförsäkringar")),
    (r"^Nordeas\b", Assign(correspondent="Nordea")),
    (r"^(Deklartion|Deklaratio)\b", Assign(correspondent="Skatteverket", document_type="Deklaration")),
    (r"Arsbesked", Assign(document_type="Årsbesked")),

    # "Årsbesked - SPP - 3", "Årsbesked 2017 - SPP": the sender comes after the word
    (r"^Årsbesked\b.*\bSPP\b", Assign(correspondent="SPP")),
    (r"^Årsbesked\b.*\bSkandia\b", Assign(correspondent="Skandia")),
    (r"^Årsbesked\b.*\bSEB\b", Assign(correspondent="SEB")),
    (r"^Årsbesked\b.*\bNordea\b", Assign(correspondent="Nordea")),
    (r"^Årsbesked\b.*\bAvanza\b", Assign(correspondent="Avanza")),
    (r"^Årsbesked\b.*\bCollectum\b", Assign(correspondent="Collectum", tags=("Pension",))),
    (r"^Årsbesked\b.*\bLänsförsäkr", Assign(correspondent="Länsförsäkringar")),

    # Tax: receipts for a filed return, and back taxes
    (r"^_?Kvittens\b.*deklaration", Assign(correspondent="Skatteverket", document_type="Deklaration")),
    (r"^Restskatt\b", Assign(correspondent="Skatteverket")),

    # Senders that will not come again
    (r"^Pricer\b", Assign(correspondent="Pricer")),
    (r"^Convini\b", Assign(correspondent="Convini")),
    (r"^Microsoft\b", Assign(correspondent="Microsoft")),
    (r"^AWS\b", Assign(correspondent="AWS")),
    (r"^Securitas\b", Assign(correspondent="Securitas")),
    (r"^Bromma Flygfält\b", Assign(correspondent="Bromma Flygfält")),
    (r"^CityOptik\b", Assign(correspondent="CityOptik", tags=("Hälsa",))),
    (r"\bUnoform\b", Assign(correspondent="Unoform")),

    # Kinds of document
    (r"^(Betyg|Examensbevis|Tjänstgöringsintyg|Studiemeritförteckning|Utbildningsbevis|Meritförteckning|Kursintyg)\b",
     Assign(document_type="Intyg")),
    (r"^Aktie(inköp|utdelning)\b", Assign(document_type="Aktienota")),

    # People. Whole words only, so "Max" does not match "Maxi"
    (r"\bMax\b", Assign(tags=("Max",))),
    (r"\bTilde\b", Assign(tags=("Tilde",))),
    (r"\bHelen\b", Assign(tags=("Helen",))),
    (r"\bFolke\b", Assign(tags=("Folke",))),
)


# ── 3. Workflows (Paperless: new documents and the backfill) ──────────────────
# One workflow per entry, in this order; when several match, a later one's correspondent and document type
# win. So: correspondents, then broad document types, then the specific ones. Each entry has a name (shown in
# Paperless), one or more filename patterns (any may match), and what to assign.

@dataclass(frozen=True)
class Workflow:
    name: str
    patterns: Tuple[str, ...]
    assign: Assign


WORKFLOWS = (
    # Correspondents
    Workflow("Avsändare: Nordea", ("Nordea*",), Assign(correspondent="Nordea")),
    Workflow("Avsändare: SEB", ("SEB*",), Assign(correspondent="SEB")),
    Workflow("Avsändare: SBAB", ("SBAB*",), Assign(correspondent="SBAB")),
    Workflow("Avsändare: SPP", ("SPP*",), Assign(correspondent="SPP")),
    Workflow("Avsändare: Skandia", ("Skandia*",), Assign(correspondent="Skandia")),
    Workflow("Avsändare: Avanza", ("Avanza*",), Assign(correspondent="Avanza")),
    Workflow("Avsändare: Collectum", ("Collectum*",), Assign(correspondent="Collectum", tags=("Pension",))),
    Workflow("Avsändare: Pensionsmyndigheten", ("PPM*", "Pensionsmyndigheten*"),
             Assign(correspondent="Pensionsmyndigheten", tags=("Pension",))),
    Workflow("Avsändare: Länsförsäkringar", ("Länsförsäkr*", "LF - *"), Assign(correspondent="Länsförsäkringar")),
    Workflow("Avsändare: Trygg Hansa", ("Trygg Hansa*", "TryggHansa*"), Assign(correspondent="Trygg Hansa")),
    Workflow("Avsändare: IF", ("IF - *",), Assign(correspondent="IF")),
    Workflow("Avsändare: Försäkringskassan", ("Försäkringskassa*",), Assign(correspondent="Försäkringskassan")),
    Workflow("Avsändare: Skatteverket",
             ("Deklaration*", "Inkomstdeklaration*", "Slutskattebesked*", "Skatteverket*", "*Orange*kuvert*"),
             Assign(correspondent="Skatteverket")),
    Workflow("Avsändare: Ellevio", ("El - Ellevio*", "Ellevio*"), Assign(correspondent="Ellevio")),
    Workflow("Avsändare: Skellefteå Kraft", ("El - Skellefteå*", "SkellefteåKraft*"),
             Assign(correspondent="Skellefteå Kraft")),
    Workflow("Avsändare: Coop", ("Coop*",), Assign(correspondent="Coop")),
    Workflow("Avsändare: ICA", ("ICA - *",), Assign(correspondent="ICA")),
    Workflow("Avsändare: SAS EuroBonus", ("SAS Eurobonus*",), Assign(correspondent="SAS EuroBonus")),
    Workflow("Avsändare: Apple", ("Apple*",), Assign(correspondent="Apple")),
    Workflow("Avsändare: Volvo", ("Volvo*",), Assign(correspondent="Volvo", tags=("Bil",))),
    Workflow("Avsändare: Hertz", ("Hertz*", "Faktura Hertz*"), Assign(correspondent="Hertz", tags=("Bil",))),
    Workflow("Avsändare: Telia", ("Telia*",), Assign(correspondent="Telia")),
    Workflow("Avsändare: SATS", ("SATS*",), Assign(correspondent="SATS")),

    # Broad document types: a word anywhere in the name
    Workflow("Typ: Årsbesked", ("*Årsbesked*",), Assign(document_type="Årsbesked")),
    Workflow("Typ: Kvartalsbesked", ("*Kvartalsbesked*",), Assign(document_type="Kvartalsbesked")),
    Workflow("Typ: Faktura", ("*Faktura*",), Assign(document_type="Faktura")),
    Workflow("Typ: Avtal", ("*Avtal*", "*Kontrakt*"), Assign(document_type="Avtal")),
    Workflow("Typ: Kvitto", ("*Kvitto*",), Assign(document_type="Kvitto")),
    Workflow("Typ: Försäkring", ("*Försäkringsbesked*", "*Försäkringsbrev*"), Assign(document_type="Försäkring")),
    # "Betalning" starting the name or after a space — not "Återbetalningsbegäran", "Utbetalningsspecifikation"
    Workflow("Typ: Betalning", ("Betalning*", "* Betalning*", "Nordea*Överför*"), Assign(document_type="Betalning")),

    # Specific document types: Hazel's rename rules and other fixed prefixes
    Workflow("Typ: Lönebesked", ("SBAB Lön*", "SBAB - Lön*"), Assign(document_type="Lönebesked")),
    Workflow("Typ: Faktura (kort, el, bolån, bil)",
             ("Nordea - Gold*", "Nordea Gold*", "SAS Eurobonus*", "El - *", "SBAB Bolån*", "Volvo - *",
              "IF - Hemförsäkr*"),
             Assign(document_type="Faktura")),
    Workflow("Typ: Deklaration",
             ("Deklaration*", "Inkomstdeklaration*", "Slutskattebesked*", "Kontrolluppgift*", "*Orange*kuvert*"),
             Assign(document_type="Deklaration")),

    # Tags
    Workflow("Tagg: Hälsa",
             ("*Hälsokontroll*", "*Sjukskriv*", "*Vaccination*", "Sofiahemmet*", "*Labbsvar*"),
             Assign(tags=("Hälsa",))),
    Workflow("Tagg: Pension", ("*Pension*",), Assign(tags=("Pension",))),
)

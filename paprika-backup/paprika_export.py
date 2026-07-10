#!/usr/bin/env python3
# =============================================================================
# paprika_export.py
#
# Generates a human-readable HTML export of all (non-trashed) Paprika recipes
# from a Paprika SQLite database. Reads the DB read-only and writes:
#
#   <output_dir>/index.html          all recipes, one page
#
# Images are NOT copied here — the backup script copies Paprika's whole
# Data/Photos tree into <output_dir>/Photos, and this page references those
# files as "Photos/<recipe-uid>/<file>". A referenced photo that is not present
# in that archive (e.g. Paprika has not downloaded it on this Mac) is counted as
# skipped and simply gets no <img>; the DB snapshot still records its filename.
#
# The purpose is durability: proof that the SQLite backup contains all recipe
# data, readable in any browser even if Paprika (the app / its sync servers)
# ever goes away. Look-and-feel is intentionally basic-but-nice; completeness
# (every meaningful field for every recipe) is what matters.
#
# Standard library only — no third-party packages.
#
# Usage:
#   paprika_export.py <db_file> <photos_dir> <output_dir>
#
#   <db_file>     path to the Paprika.sqlite snapshot (read-only)
#   <photos_dir>  the copied Photos/ archive to reference/existence-check against
#   <output_dir>  directory to write index.html into (created if missing)
#
# Prints a one-line summary on success:  recipes=N photos=M skipped=K
# Exits non-zero only on a hard failure (cannot open the database).
# =============================================================================

import html
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Core Data stores timestamps as seconds since 2001-01-01 UTC.
CORE_DATA_EPOCH_OFFSET = 978307200

BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def format_text(raw):
    """HTML-escape free text, then apply Paprika's light markdown: **bold** and
    newlines. Returns '' for empty/None."""
    if not raw:
        return ""
    escaped = html.escape(raw)
    escaped = BOLD_RE.sub(r"<strong>\1</strong>", escaped)
    return escaped.replace("\n", "<br>\n")


def format_created(value):
    """Convert a Core Data timestamp to a YYYY-MM-DD string, or '' on failure."""
    if not value:
        return ""
    try:
        dt = datetime.fromtimestamp(value + CORE_DATA_EPOCH_OFFSET, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, OverflowError, OSError):
        return ""


def find_category_join(conn):
    """Discover the Core Data many-to-many join table between recipes and
    categories. Its name/columns carry entity numbers (e.g. Z_12CATEGORIES with
    columns Z_12RECIPES / Z_13CATEGORIES) that can differ between schema
    versions, so we locate it dynamically. Returns (table, recipe_col, cat_col)
    or None if it cannot be found."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Z\\_%CATEGORIES' ESCAPE '\\'"
    ).fetchall()
    for (table,) in rows:
        cols = [c[1] for c in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]
        recipe_col = next((c for c in cols if c.endswith("RECIPES")), None)
        cat_col = next((c for c in cols if c.endswith("CATEGORIES")), None)
        if recipe_col and cat_col:
            return table, recipe_col, cat_col
    return None


def fetch_categories(conn, join, recipe_pk):
    """Return a sorted list of category names for a recipe, or [] on any error
    (categories are nice-to-have, never fatal)."""
    if not join:
        return []
    table, recipe_col, cat_col = join
    try:
        rows = conn.execute(
            f'SELECT c.ZNAME FROM "{table}" j '
            f'JOIN ZRECIPECATEGORY c ON c.Z_PK = j."{cat_col}" '
            f'WHERE j."{recipe_col}" = ? AND c.ZNAME IS NOT NULL '
            f"ORDER BY c.ZNAME COLLATE NOCASE",
            (recipe_pk,),
        ).fetchall()
        return [r[0] for r in rows]
    except sqlite3.Error:
        return []


def fetch_photo_filenames(conn, recipe_uid, primary_photo):
    """Ordered list of photo filenames for a recipe. Prefers the ZRECIPEPHOTO
    rows; falls back to the recipe's primary ZPHOTO."""
    names = []
    try:
        rows = conn.execute(
            "SELECT ZFILENAME FROM ZRECIPEPHOTO "
            "WHERE ZRECIPEUID = ? AND ZFILENAME IS NOT NULL "
            "AND (ZISPENDINGDELETION = 0 OR ZISPENDINGDELETION IS NULL) "
            "ORDER BY ZORDERFLAG",
            (recipe_uid,),
        ).fetchall()
        names = [r[0] for r in rows]
    except sqlite3.Error:
        names = []
    if not names and primary_photo:
        names = [primary_photo]
    # De-duplicate while preserving order.
    seen = set()
    return [n for n in names if not (n in seen or seen.add(n))]


def resolve_photos(photos_dir, recipe_uid, filenames):
    """Reference a recipe's photos from the copied Photos/ archive. Returns
    (rel_paths, present_count, missing_count). A referenced photo not present in
    the archive (e.g. not downloaded on this Mac) is counted as missing and gets
    no <img>; the DB snapshot still records its filename."""
    rel_paths = []
    present = missing = 0
    for name in filenames:
        if (photos_dir / recipe_uid / name).is_file():
            rel_paths.append(f"Photos/{recipe_uid}/{name}")
            present += 1
        else:
            missing += 1
    return rel_paths, present, missing


PAGE_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       margin: 0; color: #1c1c1e; background: #f5f5f7; }
.wrap { max-width: 820px; margin: 0 auto; padding: 24px 20px 64px; }
header.page { border-bottom: 2px solid #d0453b; padding-bottom: 16px; margin-bottom: 24px; }
header.page h1 { margin: 0 0 4px; font-size: 28px; }
header.page .meta { color: #6b6b70; font-size: 14px; }
nav.toc { background: #fff; border: 1px solid #e3e3e6; border-radius: 10px; padding: 16px 20px;
          margin-bottom: 28px; }
nav.toc h2 { margin: 0 0 10px; font-size: 15px; text-transform: uppercase; letter-spacing: .04em;
             color: #6b6b70; }
nav.toc ol { margin: 0; padding-left: 22px; columns: 2; column-gap: 28px; }
nav.toc a { color: #0a58ca; text-decoration: none; }
nav.toc a:hover { text-decoration: underline; }
article.recipe { background: #fff; border: 1px solid #e3e3e6; border-radius: 12px;
                 padding: 22px 24px; margin-bottom: 22px; }
article.recipe h2 { margin: 0 0 6px; font-size: 22px; color: #d0453b; }
.badges { margin: 8px 0 14px; }
.badge { display: inline-block; background: #f0f0f2; border-radius: 999px; padding: 3px 11px;
         font-size: 13px; color: #444; margin: 0 6px 6px 0; }
.photos { display: flex; flex-wrap: wrap; gap: 10px; margin: 4px 0 16px; }
.photos img { max-width: 220px; max-height: 220px; width: auto; height: auto; border-radius: 8px;
              object-fit: cover; }
.section { margin-top: 16px; }
.section h3 { margin: 0 0 6px; font-size: 15px; text-transform: uppercase; letter-spacing: .03em;
              color: #6b6b70; }
.section .body { white-space: normal; }
.rating { color: #e6a100; letter-spacing: 2px; }
a.source { color: #0a58ca; }
footer.page { color: #9a9aa0; font-size: 13px; text-align: center; margin-top: 32px; }
@media (prefers-color-scheme: dark) {
  body { color: #e6e6ea; background: #1a1a1c; }
  nav.toc, article.recipe { background: #232326; border-color: #3a3a3e; }
  .badge { background: #333338; color: #cfcfd4; }
  nav.toc a, a.source { color: #6ea8fe; }
}
@media print { body { background: #fff; } nav.toc, article.recipe { border: none; } }
"""


def anchor_id(uid):
    return "r-" + re.sub(r"[^A-Za-z0-9_-]", "", uid or "")


def render_section(title, raw):
    body = format_text(raw)
    if not body:
        return ""
    return (
        f'<div class="section"><h3>{html.escape(title)}</h3>'
        f'<div class="body">{body}</div></div>'
    )


def render_recipe(r, categories, photo_paths):
    uid = r["ZUID"] or ""
    name = html.escape(r["ZNAME"] or "(namnlös)")
    parts = [f'<article class="recipe" id="{anchor_id(uid)}">', f"<h2>{name}</h2>"]

    # Badges: servings / times / difficulty / rating / created.
    badges = []
    for label, key in (
        ("Portioner", "ZSERVINGS"),
        ("Förberedelse", "ZPREPTIME"),
        ("Tillagning", "ZCOOKTIME"),
        ("Total tid", "ZTOTALTIME"),
        ("Svårighet", "ZDIFFICULTY"),
    ):
        val = r[key]
        if val:
            badges.append(f'<span class="badge">{html.escape(label)}: {html.escape(str(val))}</span>')
    rating = r["ZRATING"] or 0
    if rating:
        stars = "★" * int(rating) + "☆" * (5 - int(rating))
        badges.append(f'<span class="badge rating">{stars}</span>')
    if categories:
        cats = ", ".join(html.escape(c) for c in categories)
        badges.append(f'<span class="badge">Kategorier: {cats}</span>')
    created = format_created(r["ZCREATED"])
    if created:
        badges.append(f'<span class="badge">Tillagd: {created}</span>')
    if badges:
        parts.append('<div class="badges">' + "".join(badges) + "</div>")

    # Photos.
    if photo_paths:
        imgs = "".join(f'<img src="{html.escape(p)}" alt="{name}">' for p in photo_paths)
        parts.append(f'<div class="photos">{imgs}</div>')

    # Source (as link if a URL exists).
    source = r["ZSOURCE"]
    source_url = r["ZSOURCEURL"]
    if source_url:
        parts.append(
            '<div class="section"><h3>Källa</h3><div class="body">'
            f'<a class="source" href="{html.escape(source_url)}">'
            f'{html.escape(source or source_url)}</a></div></div>'
        )
    elif source:
        parts.append(render_section("Källa", source))

    # Main text sections.
    parts.append(render_section("Beskrivning", r["ZDESCRIPTIONTEXT"]))
    parts.append(render_section("Ingredienser", r["ZINGREDIENTS"]))
    parts.append(render_section("Gör så här", r["ZDIRECTIONS"]))
    parts.append(render_section("Anteckningar", r["ZNOTES"]))
    parts.append(render_section("Näringsinformation", r["ZNUTRITIONALINFO"]))

    parts.append("</article>")
    return "".join(parts)


def main(argv):
    if len(argv) != 4:
        print("usage: paprika_export.py <db_file> <photos_dir> <output_dir>", file=sys.stderr)
        return 2

    db_file = Path(argv[1])
    photos_dir = Path(argv[2])
    output_dir = Path(argv[3])
    output_dir.mkdir(parents=True, exist_ok=True)

    # immutable=1 opens read-only AND tells SQLite the file won't change, so it
    # never creates -wal/-shm sidecars next to the snapshot. The snapshot from
    # `sqlite3 .backup` is a fully checkpointed single file, so this reads all data.
    try:
        conn = sqlite3.connect(f"file:{db_file}?immutable=1", uri=True)
    except sqlite3.Error as exc:
        print(f"cannot open database {db_file}: {exc}", file=sys.stderr)
        return 1
    conn.row_factory = sqlite3.Row

    try:
        recipes = conn.execute(
            "SELECT Z_PK, ZUID, ZNAME, ZDESCRIPTIONTEXT, ZINGREDIENTS, ZDIRECTIONS, ZNOTES, "
            "ZNUTRITIONALINFO, ZSERVINGS, ZPREPTIME, ZCOOKTIME, ZTOTALTIME, ZDIFFICULTY, "
            "ZRATING, ZSOURCE, ZSOURCEURL, ZPHOTO, ZCREATED "
            "FROM ZRECIPE WHERE ZINTRASH = 0 ORDER BY ZNAME COLLATE NOCASE"
        ).fetchall()
    except sqlite3.Error as exc:
        print(f"cannot read recipes: {exc}", file=sys.stderr)
        return 1

    join = find_category_join(conn)

    total_photos = total_skipped = 0
    toc_items = []
    recipe_html = []

    for r in recipes:
        categories = fetch_categories(conn, join, r["Z_PK"])
        filenames = fetch_photo_filenames(conn, r["ZUID"], r["ZPHOTO"])
        photo_paths, present, skipped = resolve_photos(photos_dir, r["ZUID"] or "", filenames)
        total_photos += present
        total_skipped += skipped

        name = html.escape(r["ZNAME"] or "(namnlös)")
        toc_items.append(f'<li><a href="#{anchor_id(r["ZUID"] or "")}">{name}</a></li>')
        recipe_html.append(render_recipe(r, categories, photo_paths))

    conn.close()

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    page = (
        "<!doctype html>\n"
        '<html lang="sv">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>Paprika-recept — säkerhetskopia {generated[:10]}</title>\n"
        f"<style>{PAGE_CSS}</style>\n</head>\n<body>\n<div class=\"wrap\">\n"
        '<header class="page">\n'
        "<h1>Paprika-recept</h1>\n"
        f'<div class="meta">Säkerhetskopia genererad {generated} · {len(recipes)} recept</div>\n'
        "</header>\n"
        '<nav class="toc">\n<h2>Innehåll</h2>\n<ol>\n'
        + "\n".join(toc_items)
        + "\n</ol>\n</nav>\n"
        + "\n".join(recipe_html)
        + '\n<footer class="page">Genererad från Paprika SQLite-databasen · '
        f"{len(recipes)} recept, {total_photos} bilder</footer>\n"
        "</div>\n</body>\n</html>\n"
    )

    (output_dir / "index.html").write_text(page, encoding="utf-8")

    print(f"recipes={len(recipes)} photos={total_photos} skipped={total_skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

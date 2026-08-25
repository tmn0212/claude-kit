"""A queryable index over everything a project has written down.

By the time a project has a year of design notes, its `docs/` tree holds
thousands of lines a future session would otherwise re-derive or grep blindly.
This indexes them so a question is one query.

**The markdown stays authoritative.** This database is derived and disposable;
delete it and `kb build` reconstructs it. The readable artifact is the truth,
the convenient artifact is generated.

Stdlib only. Python ships sqlite3 with FTS5, so this adds no dependency.

    kb build                     (re)index. Fast; run it after writing anything
    kb search "damage tracking"  full text across every indexed document
    kb why "cache invalidation"  the DECISION, its evidence, and who cites it
    kb row 1.10                  ONE table row, paired with its headers
    kb section <name> <heading>  pull ONE section out of a document, correctly
    kb headings <name>           table of contents; replaces grep '^#'
    kb pack <topic> [--budget N] ONE bounded briefing: decision plus evidence
    kb subjects [filter]         what names `section` and `headings` accept
    kb stale                     which sources the index is behind
    kb stats

Reach for `search` first, then narrow. A numbered fact ("1.10", "4.9") is
usually a table ROW, so `row` returns the fact where `section` returns the
whole chapter.

`pack` is for "what do we know about X" rather than a specific lookup. Search
plus several sections is four or five requests, and each one re-reads the whole
context window; `pack` composes the same answer in one, spends a token budget,
then names what it left out with the exact command to fetch each piece.

`why` is for before you change something. It joins the decision to the evidence
it rests on and to the reverse edge: what else cites it, which is what says how
far a change reaches.

**The index self-heals.** Every read checks the sources' mtime and size and
rebuilds when they moved, because an answer from an index that predates the
edit is indistinguishable from a correct one. Set `KB_NO_AUTOBUILD=1` to warn
instead.

Prints `KB OK`. Grep for the line, not exit code 0.
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

from kit_config import Config, rel

DB_NAME = ".kb.sqlite"

SCHEMA = """
-- mtime and size are what make staleness detectable. Without them the index
-- answers confidently from whatever it was built from, and a stale answer is
-- indistinguishable from a correct one.
CREATE TABLE doc(id INTEGER PRIMARY KEY, path TEXT UNIQUE, kind TEXT,
                 subject TEXT, lines INT, mtime INT, size INT);
-- One row per markdown section, because a section is the unit a reader
-- actually wants back. Whole-file hits are useless at 900 lines.
CREATE TABLE section(id INTEGER PRIMARY KEY, doc_id INT, heading TEXT,
                     line INT, body TEXT, level INT);
-- One row per markdown TABLE row. A numbered fact is often a table row, not a
-- heading, and section granularity returns a whole chapter for those.
CREATE TABLE trow(id INTEGER PRIMARY KEY, doc_id INT, key TEXT, line INT,
                  header TEXT, body TEXT);
-- Front matter, and what each document points at. Together these turn separate
-- trees into one graph: a decision, the note that motivated it, the measurement
-- that backs it.
CREATE TABLE docmeta(doc_id INT, key TEXT, value TEXT);
CREATE TABLE ref(src INT, kind TEXT, target TEXT);
CREATE VIRTUAL TABLE fts USING fts5(heading, body, content='section',
                                    content_rowid='id', tokenize='porter');
CREATE INDEX section_doc ON section(doc_id);
CREATE INDEX trow_key ON trow(key);
CREATE INDEX docmeta_doc ON docmeta(doc_id);
CREATE INDEX docmeta_kv ON docmeta(key, value);
CREATE INDEX ref_src ON ref(src);
CREATE INDEX ref_target ON ref(kind, target);
"""


class Kb:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.root = cfg.root
        self.db_path = cfg.root / DB_NAME
        self.sources = [str(s).strip("/") for s in cfg.get("kb.sources", [])]
        self.exts = tuple(cfg.get("kb.extensions", [".md"]))
        self.skip = set(cfg.get("kb.skip_dirs", []))
        self.generic = {s.lower() for s in cfg.get("kb.generic_stems", [])}
        self.aliases = {k.lower(): v for k, v in (cfg.get("kb.aliases", {}) or {}).items()}
        self.decisions = str(cfg.get("adr.dir", "")).strip("/")
        evidence = cfg.get("kb.evidence_dirs", None)
        if evidence is None:
            evidence = [s for s in self.sources if s != self.decisions]
        self.evidence = [str(e).strip("/") for e in evidence]
        # A path reference is only recognised inside a directory this project
        # actually has. Deriving it from config is what stops the regex being a
        # hardcoded list of one project's folder names.
        roots = sorted({s.split("/")[0] for s in self.sources} | {"src", "tests", "tools"})
        self._path_ref = re.compile(
            r"\b((?:" + "|".join(re.escape(r) for r in roots) + r")/[\w./+-]*\w)"
        )

    # -- storage -----------------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db

    def healthy(self) -> bool:
        """Whether the index file exists AND holds a usable schema.

        Existence alone is not enough, and assuming it was the worst bug this
        tool has had: `sqlite3.connect` CREATES an empty file, so any read path
        that connected before checking left a 0-byte database behind. The build
        path then saw a file present, skipped the build, and every query failed
        with `no such table: fts` until somebody ran `kb build` by hand.
        """
        try:
            if not self.db_path.exists() or self.db_path.stat().st_size == 0:
                return False
        except OSError:
            return False
        try:
            db = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            try:
                db.execute("SELECT 1 FROM doc LIMIT 1").fetchone()
                db.execute("SELECT 1 FROM fts LIMIT 1").fetchone()
            finally:
                db.close()
            return True
        except sqlite3.Error:
            return False

    def walk(self):
        """Yield (source, relpath, abspath) for every document in the index.

        One definition, used by both the builder and the staleness check, so
        those two can never disagree about what "indexed" means.
        """
        # One yield per relative path. Without this, a config naming the same
        # tree twice ("docs" and "docs/", which strip() makes identical) yields
        # every file twice; build() drops the duplicate with INSERT OR IGNORE
        # while stale() counts it, so the index reports drift forever and every
        # single query pays for a full rebuild.
        seen: set[str] = set()
        for src in self.sources:
            base = self.root / src
            if not base.is_dir():
                continue
            for dirpath, dirs, files in os.walk(base):
                dirs[:] = [d for d in sorted(dirs) if d not in self.skip]
                for name in sorted(files):
                    if not name.endswith(self.exts):
                        continue
                    path = Path(dirpath) / name
                    relative = rel(path, self.root)
                    # A nested source wins over its parent: source trees can
                    # overlap, and a document indexed twice returns duplicate
                    # hits for every query.
                    if any(
                        relative.startswith(other + "/") and other != src and len(other) > len(src)
                        for other in self.sources
                    ):
                        continue
                    if relative in seen:
                        continue
                    # A file build() cannot read is a file it will not index, so
                    # counting it here as "added" is the other half of the same
                    # disagreement. Skip it in both places or in neither.
                    if not os.access(path, os.R_OK):
                        continue
                    seen.add(relative)
                    yield src, relative, path

    def subject_of(self, path: Path, src: str) -> str:
        stem = path.stem
        if stem.lower() in self.generic:
            return f"{path.parent.name}-{stem.lower()}"
        return stem

    def build(self, quiet: bool = False) -> int:
        # Build into a temp file and rename over the old index. Deleting first
        # leaves a window where a concurrent session's query finds no index at
        # all, and sessions do run in parallel. os.replace is atomic.
        tmp = self.db_path.with_suffix(".sqlite.building")
        for leftover in (tmp, Path(str(tmp) + "-journal")):
            if leftover.exists():
                leftover.unlink()
        db = sqlite3.connect(tmp)
        db.row_factory = sqlite3.Row
        db.executescript(SCHEMA)

        ndoc = nsec = nrow = 0
        for src, relative, path in self.walk():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                stat = path.stat()
            except OSError:
                continue
            cur = db.execute(
                "INSERT OR IGNORE INTO doc(path,kind,subject,lines,mtime,size)"
                " VALUES(?,?,?,?,?,?)",
                (
                    relative,
                    src,
                    self.subject_of(path, src),
                    text.count("\n") + 1,
                    int(stat.st_mtime),
                    stat.st_size,
                ),
            )
            if not cur.rowcount:
                continue
            doc_id = cur.lastrowid
            ndoc += 1
            for heading, line, body, level in split_sections(text):
                db.execute(
                    "INSERT INTO section(doc_id,heading,line,body,level) VALUES(?,?,?,?,?)",
                    (doc_id, heading, line, body, level),
                )
                nsec += 1
            for key, line, header, body in split_tables(text):
                db.execute(
                    "INSERT INTO trow(doc_id,key,line,header,body) VALUES(?,?,?,?,?)",
                    (doc_id, key, line, header, body),
                )
                nrow += 1
            meta = parse_frontmatter(text)
            # A record's number is its identity for "who cites this". Take it
            # from front matter when present and from the filename otherwise,
            # because the numbered filename is the convention every one follows.
            # Tested on the PATH, not on `src`: the decisions directory is
            # normally nested inside a source tree, so comparing against the
            # source name never matches.
            if self.decisions and relative.startswith(self.decisions + "/"):
                filed = re.match(r"(\d{3,4})", path.stem)
                if filed:
                    meta.setdefault("id", filed.group(1).zfill(4))
            for key, value in meta.items():
                db.execute(
                    "INSERT INTO docmeta(doc_id,key,value) VALUES(?,?,?)", (doc_id, key, value)
                )
            for kind, target in self.parse_refs(text):
                db.execute("INSERT INTO ref(src,kind,target) VALUES(?,?,?)", (doc_id, kind, target))

        db.execute(
            "INSERT INTO fts(rowid,heading,body) SELECT id,heading,body FROM section WHERE body != ''"
        )
        db.commit()
        db.close()
        os.replace(tmp, self.db_path)
        if not quiet:
            print(f"indexed {ndoc} documents, {nsec} sections, {nrow} table rows -> {DB_NAME}")
            print("KB OK")
        return 0

    def parse_refs(self, text: str):
        """(kind, target) for everything a document points at.

        Two kinds: a decision reference in prose, and a plain repo path.
        """
        refs = set()
        for match in _ADR_REF.finditer(text):
            refs.add(("adr", match.group(1).zfill(4)))
        for match in self._path_ref.finditer(text):
            target = match.group(1).rstrip(".,;:)")
            if "/" in target:
                refs.add(("path", target))
        return refs

    # -- freshness ---------------------------------------------------------
    def stale(self):
        """(added, changed, removed): the filesystem against the index.

        Opens read-only through a URI, so a missing index stays missing rather
        than being created as an empty file by the act of asking about it.
        """
        if not self.healthy():
            return sorted(relative for _s, relative, _p in self.walk()), [], []
        try:
            db = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            db.row_factory = sqlite3.Row
            try:
                indexed = {
                    r["path"]: (r["mtime"], r["size"])
                    for r in db.execute("SELECT path, mtime, size FROM doc")
                }
            finally:
                db.close()
        except sqlite3.Error:
            return [], [], []
        added, changed = [], []
        for _src, relative, path in self.walk():
            try:
                stat = path.stat()
            except OSError:
                continue
            previous = indexed.pop(relative, None)
            if previous is None:
                added.append(relative)
            elif int(stat.st_mtime) != previous[0] or stat.st_size != previous[1]:
                changed.append(relative)
        return sorted(added), sorted(changed), sorted(indexed)

    def refresh(self) -> bool:
        """Rebuild if the sources moved.

        Rebuilding rather than only warning, because the failure this guards
        against is a confident answer from a stale index, and a warning the
        reader has to act on is one step from being ignored.
        """
        auto = not os.environ.get("KB_NO_AUTOBUILD")
        added, changed, removed = self.stale()
        total = len(added) + len(changed) + len(removed)
        if not total:
            return False
        detail = ", ".join(
            f"{len(group)} {label}"
            for group, label in ((added, "added"), (changed, "changed"), (removed, "removed"))
            if group
        )
        example = (added + changed + removed)[0]
        if not auto:
            sys.stderr.write(
                f"kb: WARNING index is behind the sources ({detail}; e.g. {example}). Run `kb build`\n"
            )
            return False
        sys.stderr.write(f"kb: sources moved ({detail}; e.g. {example}), rebuilding\n")
        self.build(quiet=True)
        return True

    def ready(self) -> None:
        if not self.healthy():
            sys.stderr.write("kb: no usable index, building one\n")
            self.build(quiet=True)
        else:
            self.refresh()

    # -- query -------------------------------------------------------------
    def match(self, db, sql: str, query: str, tail: tuple = ()):
        """Run an FTS query, retrying with a tokenised form on a syntax error.

        `fts_query` passes a deliberate FTS5 expression through untouched, but a
        plausible question looks like one: `ratio 2:1` has a colon, `C++ (draft)`
        has parentheses. Both raise, and a syntax error sends the reader back to
        grep, which is the failure this whole tool exists to prevent. So the
        passthrough is a guess, and this un-guesses it.
        """
        error = None
        for attempt in (self.fts_query(query), self.fts_query(query, tokens_only=True)):
            if attempt is None:
                # Nothing in the query tokenises, so there is nothing to ask.
                # Running it anyway is a syntax error, and a syntax error sends
                # the reader back to grep.
                return []
            try:
                return db.execute(sql, (attempt, *tail)).fetchall()
            except sqlite3.OperationalError as exc:
                error = exc
        raise error

    def fts_query(self, query: str, tokens_only: bool = False) -> str | None:
        """Turn a human question into valid FTS5.

        Two rules, both learned from queries that failed:

        1. Bare terms are OR-ed. FTS5 ANDs them, so a four-word question
           requires all four in one section and usually returns nothing.
        2. Any term carrying punctuation is quoted. FTS5 treats `.`, `-` and
           `/` as syntax and raises a syntax error on a bare `v1.3`. That error
           sends the reader straight back to grep, which is the failure this
           tool exists to prevent.
        """
        # Pass through only what is UNAMBIGUOUSLY a deliberate expression: a
        # boolean operator, or a balanced quoted phrase. The old test also fired
        # on a bare `:`, `(` or `*`, which made `ratio 2:1` a column filter that
        # parsed and matched nothing - worse than an error, because it looked
        # like an answer.
        if (
            not tokens_only
            and query.count('"') % 2 == 0
            and (re.search(r'"[^"]+"', query) or re.search(r"\b(AND|OR|NOT|NEAR)\b", query))
        ):
            return query
        terms = []
        for token in query.split():
            if not re.search(r"\w", token):
                continue
            # Quote EVERYTHING on the retry. A bare `AND` is an operator, so the
            # fallback for a query containing one has to be total or it fails
            # exactly where it is needed most.
            if tokens_only or not re.fullmatch(r"\w+", token):
                one = '"%s"' % token.replace('"', "")
            else:
                one = token
            alias = self.aliases.get(token.lower().strip('"'))
            # A string is one alias, not a sequence of letters. Writing
            # `dma = "direct memory access"` is the natural spelling, and
            # iterating it produced a 20-way OR of single characters that
            # matched every document in the index.
            if isinstance(alias, str):
                alias = [alias]
            if alias:
                group = [one] + [
                    a if re.fullmatch(r"\w+", a) else '"%s"' % a for a in alias
                ]
                one = "(" + " OR ".join(group) + ")"
            terms.append(one)
        return " OR ".join(terms) if terms else None

    def search(self, query: str, limit: int = 12) -> int:
        db = self.connect()
        try:
            rows = self.match(
                db,
                """
                SELECT d.path, d.subject, s.heading, s.line,
                       snippet(fts, 1, '>>>', '<<<', ' ... ', 24) AS snip
                FROM fts JOIN section s ON s.id = fts.rowid JOIN doc d ON d.id = s.doc_id
                WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT ?""",
                query,
                (limit,),
            )
        except sqlite3.OperationalError as exc:
            sys.stderr.write(f"error: bad query: {exc}\n")
            sys.stderr.write('       FTS5 syntax: bare words, "quoted phrase", AND/OR/NOT\n')
            return 2
        if not rows:
            print("no hits")
            return 0
        for row_ in rows:
            print(f"\n{row_['path']}:{row_['line']}  [{row_['subject']}]")
            print(f"  ## {row_['heading']}")
            print(f"  {' '.join(row_['snip'].split())[:300]}")
        print(f"\n{len(rows)} hit(s)")
        print("KB OK")
        return 0

    def why(self, topic: str, limit: int = 3) -> int:
        """The decision on a topic, the evidence it rests on, and who leans on it.

        Answers the question actually asked when picking work back up: why is
        it this way, and what would I break. That otherwise takes three queries
        across three trees.
        """
        if not self.decisions:
            print("no decision directory configured (set adr.dir in claude-kit.toml)")
            return 1
        db = self.connect()
        try:
            # Matched on the PATH prefix, not on `kind`. `kind` is the source
            # tree a document was walked from, and the decisions directory is
            # normally nested inside one of those ("docs" holding
            # "docs/decisions"), so a kind comparison silently matches nothing.
            # Restricted to documents carrying a record number, which excludes
            # the generated index: it matches every decision query and is a
            # decision about nothing.
            rows = self.match(
                db,
                """
                SELECT DISTINCT d.id, d.path, d.subject
                FROM fts JOIN section s ON s.id = fts.rowid JOIN doc d ON d.id = s.doc_id
                JOIN docmeta m ON m.doc_id = d.id AND m.key = 'id'
                WHERE fts MATCH ? AND d.path LIKE ?
                ORDER BY bm25(fts) LIMIT ?""",
                topic,
                (self.decisions + "/%", limit),
            )
        except sqlite3.OperationalError as exc:
            sys.stderr.write(f"error: bad query: {exc}\n")
            return 2
        if not rows:
            print(f"no decision matching '{topic}'")
            print(f'try: kb search "{topic}"')
            return 1

        for row_ in rows:
            meta = {
                m["key"]: m["value"]
                for m in db.execute("SELECT key, value FROM docmeta WHERE doc_id = ?", (row_["id"],))
            }
            adr_id = meta.get("id", "?")
            print(f"\n=== ADR {adr_id}  {meta.get('title', row_['subject'])}")
            print(
                f"    {meta.get('status', 'status unknown')}"
                f"  {meta.get('date', '')}  {row_['path']}"
            )
            if meta.get("applies_to"):
                print(f"    applies to: {meta['applies_to']}")
            for line in decision_lines(db, row_["id"]):
                print(f"    {line}")

            cites = [
                t["target"]
                for t in db.execute(
                    "SELECT DISTINCT target FROM ref WHERE src = ? AND kind = 'path' ORDER BY target",
                    (row_["id"],),
                )
                # Excluding the decisions tree explicitly, because the default
                # evidence list is "every source", and the decisions directory is
                # normally NESTED inside one of them rather than equal to one. A
                # decision citing another decision is not evidence for itself.
                if any(t["target"].startswith(e + "/") for e in self.evidence)
                and not (self.decisions and t["target"].startswith(self.decisions + "/"))
            ]
            if cites:
                print("    rests on:")
                for cite in cites[:8]:
                    print(f"      {cite}")

            related = sorted(
                {
                    t["target"]
                    for t in db.execute(
                        "SELECT target FROM ref WHERE src = ? AND kind = 'adr'", (row_["id"],)
                    )
                    if t["target"] != adr_id
                }
            )
            if related:
                print(f"    references ADR: {', '.join(related)}")

            # The reverse edge. This is what answers "what breaks if I revisit it".
            if adr_id != "?":
                cited_by = [
                    c["path"]
                    for c in db.execute(
                        """
                        SELECT DISTINCT d.path FROM ref r JOIN doc d ON d.id = r.src
                        WHERE r.kind = 'adr' AND r.target = ? AND d.path NOT LIKE ?
                        ORDER BY d.path""",
                        (adr_id, f"%{adr_id}-%"),
                    )
                ]
                if cited_by:
                    print(f"    cited by ({len(cited_by)}):")
                    for cite in cited_by[:8]:
                        print(f"      {cite}")
                    if len(cited_by) > 8:
                        print(f"      ... and {len(cited_by) - 8} more")
        print(f"\n{len(rows)} decision(s)")
        print("KB OK")
        return 0

    def row(self, key_query: str, subject: str | None = None) -> int:
        """Whole table rows whose first cell matches, paired with their headers.

        The unit `section` cannot reach. A row on its own is not interpretable:
        the header says which column is the question and which the finding, so
        it is stored with every row.
        """
        db = self.connect()
        sql = (
            "SELECT d.path, d.subject, t.key, t.line, t.header, t.body "
            "FROM trow t JOIN doc d ON d.id = t.doc_id WHERE lower(t.key) LIKE ?"
        )
        args: list = [f"%{key_query.lower()}%"]
        if subject:
            sql += " AND d.subject = ?"
            args.append(subject)
        rows = db.execute(sql + " ORDER BY d.path, t.line LIMIT 25", args).fetchall()
        if not rows:
            print(f"no table row whose first cell matches '{key_query}'")
            print(f'try: kb search "{key_query}"')
            return 1
        for row_ in rows:
            print(f"\n{row_['path']}:{row_['line']}  [{row_['subject']}]")
            heads = row_["header"].split(" | ")
            cells = row_["body"].split(" | ")
            width = max((len(h) for h in heads[: len(cells)]), default=0)
            for index, cell in enumerate(cells):
                head = heads[index] if index < len(heads) else f"col{index + 1}"
                if not cell.strip():
                    continue
                print(f"  {head:>{width}} : {cell}")
        print(f"\n{len(rows)} row(s)")
        print("KB OK")
        return 0

    def headings(self, subject: str) -> int:
        db = self.connect()
        rows = db.execute(
            """
            SELECT d.path, s.heading, s.line FROM section s JOIN doc d ON d.id=s.doc_id
            WHERE d.subject = ? ORDER BY d.path, s.line""",
            (subject,),
        ).fetchall()
        if not rows:
            print(f"no subject '{subject}'. Nearest:")
            like = db.execute(
                "SELECT DISTINCT subject FROM doc WHERE subject LIKE ? LIMIT 10",
                (f"%{subject}%",),
            ).fetchall()
            fallback = like or db.execute(
                "SELECT DISTINCT subject FROM doc ORDER BY subject LIMIT 20"
            ).fetchall()
            for row_ in fallback:
                print(f"  {row_['subject']}")
            return 1
        path = None
        for row_ in rows:
            if row_["path"] != path:
                path = row_["path"]
                print(f"\n{path}")
            print(f"  {row_['line']:>5}  {row_['heading']}")
        print(f"\n{len(rows)} heading(s)")
        print("KB OK")
        return 0

    def section(self, subject: str, heading_query: str) -> int:
        """One whole section. The correct replacement for a sed range."""
        db = self.connect()
        rows = db.execute(
            """
            SELECT d.path, s.heading, s.line, s.body FROM section s JOIN doc d ON d.id=s.doc_id
            WHERE d.subject = ? AND lower(s.heading) LIKE ?
            ORDER BY s.line""",
            (subject, f"%{heading_query.lower()}%"),
        ).fetchall()
        if not rows:
            # A miss that does not say what would have hit sends the reader
            # back to grep, which is what this replaces.
            print(f"no section matching '{heading_query}' in subject '{subject}'")
            available = db.execute(
                """
                SELECT s.heading FROM section s JOIN doc d ON d.id=s.doc_id
                WHERE d.subject = ? ORDER BY s.line LIMIT 40""",
                (subject,),
            ).fetchall()
            if available:
                print(f"headings in '{subject}':")
                for row_ in available:
                    print(f"  {row_['heading']}")
                print("\nthe text may be inside a table rather than under its own heading;")
                print(f'try: kb search "{heading_query}"')
            else:
                print("no such subject. Try: kb subjects")
            return 1
        for row_ in rows:
            print(f"=== {row_['path']}:{row_['line']}  ## {row_['heading']}")
            print(row_["body"])
            print()
        print("KB OK")
        return 0

    def pack(self, topic: str, budget: int = 2500) -> int:
        """One bounded briefing on a topic, instead of five separate lookups.

        The budget is the point, not a detail. An unbounded pack is a whole-file
        read wearing a hat. So it spends a budget, stops, and then NAMES what it
        left out with the command to fetch each piece. Being told precisely what
        was elided is what makes a truncated answer safe to act on.
        """
        # A budget at or below zero slices from the END of a section and calls
        # the result truncated, which is worse than useless.
        budget = max(50, budget)
        db = self.connect()
        try:
            rows = self.match(
                db,
                """
                SELECT d.path, d.subject, s.heading, s.line, s.body
                FROM fts JOIN section s ON s.id = fts.rowid JOIN doc d ON d.id = s.doc_id
                WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT 40""",
                topic,
            )
        except sqlite3.OperationalError as exc:
            sys.stderr.write(f"error: bad query: {exc}\n")
            return 2
        if not rows:
            print(f"no hits for '{topic}'. Try: kb search '{topic}'")
            return 1

        decisions = self.decisions

        def body_of(row_):
            """A record's front-matter block ranks well and is mostly
            bookkeeping. Its two useful fields are status and title, and status
            is the one a session most needs: acting on a `proposed` decision as
            though it were `accepted` is a real error."""
            if row_["heading"] == "(preamble)" and is_decision(row_):
                meta = parse_frontmatter(row_["body"]) or {}
                if meta.get("title"):
                    return (
                        f"ADR {meta.get('id', '?')} [{meta.get('status', 'status unknown')}]"
                        f" {meta['title']}"
                    )
            return row_["body"]

        def cost(row_):
            return max(1, len(body_of(row_)) // 4)

        def is_decision(row_):
            # The trailing slash matters: without it `docs/decisions-archive/`
            # reads as a decision and outranks real evidence in the ordering.
            return bool(decisions) and row_["path"].startswith(decisions + "/")

        # A decision outranks evidence: it is the thing a session most often
        # needs and most often re-derives.
        ordered = [r for r in rows if is_decision(r)] + [r for r in rows if not is_decision(r)]

        spent, shown, skipped = 0, [], []
        for row_ in ordered:
            price = cost(row_)
            if spent + price <= budget:
                shown.append((row_, body_of(row_)))
                spent += price
            elif not shown and price > budget:
                # One oversized section and nothing else fits: give the head of
                # it rather than an empty pack, and say plainly that it is cut.
                shown.append((row_, body_of(row_)[: budget * 4] + "\n[... truncated]"))
                spent = budget
            else:
                skipped.append(row_)

        print(
            f"PACK  {topic}   ~{spent} tokens of a {budget} budget, "
            f"{len(shown)} section(s) of {len(rows)} matched"
        )
        for row_, body in shown:
            tag = "DECISION" if is_decision(row_) else "section"
            print(f"\n=== [{tag}] {row_['path']}:{row_['line']}  ## {row_['heading']}")
            print(body.rstrip())
        if skipped:
            print(f"\nNot included ({len(skipped)}). Ask for any of these by name:")
            for row_ in skipped[:12]:
                print(
                    f"  ~{cost(row_):>5} tok  kb section {row_['subject']} "
                    f"'{row_['heading'][:44]}'"
                )
            if len(skipped) > 12:
                print(f"  ... and {len(skipped) - 12} more; kb search '{topic}' lists them")
        print("\nKB OK")
        return 0

    def subjects(self, filt: str | None = None) -> int:
        db = self.connect()
        sql = """SELECT kind, subject, count(*) n, sum(lines) l FROM doc
                 {where} GROUP BY kind, subject ORDER BY kind, subject"""
        if filt:
            rows = db.execute(sql.format(where="WHERE subject LIKE ?"), (f"%{filt}%",)).fetchall()
        else:
            rows = db.execute(sql.format(where="")).fetchall()
        if not rows:
            print(f"no subject matching '{filt}'")
            return 1
        kind = None
        for row_ in rows:
            if row_["kind"] != kind:
                kind = row_["kind"]
                print(f"\n{kind}")
            docs = f" ({row_['n']} files)" if row_["n"] > 1 else ""
            print(f"  {row_['subject']:<28} {row_['l']:>6} lines{docs}")
        print(f"\n{len(rows)} subject(s)")
        print("KB OK")
        return 0

    def stats(self) -> int:
        db = self.connect()
        size = self.db_path.stat().st_size // 1024
        print(f"database   {DB_NAME} ({size} KB, derived: delete and rebuild freely)")
        print(f"root       {self.root}")
        print(f"sources    {', '.join(self.sources) or '(none configured)'}")
        for row_ in db.execute(
            "SELECT kind, count(*) n, sum(lines) l FROM doc GROUP BY kind ORDER BY l DESC"
        ):
            print(f"  {row_['kind']:<18} {row_['n']:>4} docs  {row_['l']:>7} lines")
        count = db.execute("SELECT count(*) c FROM section").fetchone()["c"]
        print(f"  {'sections indexed':<18} {count:>4}")
        print("KB OK")
        return 0


# --- parsing --------------------------------------------------------------

_ADR_REF = re.compile(r"\bADR[\s-]*(\d{3,4})\b", re.I)
_CELL_NOISE = re.compile(r"[`*_]|\[([^\]]*)\]\([^)]*\)")
_SEPARATOR = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")


def split_sections(text: str):
    """Split on markdown headings, tracking fenced code.

    This is the whole reason the tool exists rather than a shell one-liner. A
    `sed -n '/heading/,/^```$/p'` range terminates on the OPENING fence and
    silently returns an empty section.

    The heading level travels with each section. Without it a heading whose
    body is empty simply vanishes, and "## Decision" directly followed by
    "### The contract" is exactly that shape.
    """
    lines = text.split("\n")
    out, heading, start, buf, fence, level = [], "(preamble)", 1, [], False, 1
    for number, line in enumerate(lines, 1):
        if line.lstrip().startswith("```"):
            fence = not fence
        match = None if fence else re.match(r"^(#{1,6})\s+\S", line)
        if match:
            if buf:
                out.append((heading, start, "\n".join(buf).strip(), level))
            heading = line.lstrip("#").strip()
            level = len(match.group(1))
            start, buf = number, []
        else:
            buf.append(line)
    if buf:
        out.append((heading, start, "\n".join(buf).strip(), level))
    # Empty sections are KEPT. Dropping them loses the structure: a "## Decision"
    # heading whose prose lives under "### The contract" has no body of its own,
    # so dropping it makes the decision unreachable from its own name. They are
    # excluded from the full-text index instead.
    return out


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def split_tables(text: str):
    """Yield (key, line, header, body) for every DATA row of every table.

    The header row is stored with each data row on purpose. A row on its own is
    not interpretable until you know which column is the question and which the
    finding.
    """
    out = []
    lines = text.split("\n")
    fence = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.lstrip().startswith("```"):
            fence = not fence
            index += 1
            continue
        # A table is a header line, then a separator, then data rows. Requiring
        # the separator is what stops a prose line containing a pipe starting one.
        if (
            not fence
            and "|" in line
            and index + 1 < len(lines)
            and _SEPARATOR.match(lines[index + 1])
            and "|" in lines[index + 1]
        ):
            header = _cells(line)
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                if lines[index].lstrip().startswith("```"):
                    break
                cells = _cells(lines[index])
                if any(c for c in cells):
                    key = _CELL_NOISE.sub(r"\1", cells[0]).strip()
                    if key:
                        out.append((key, index + 1, " | ".join(header), " | ".join(cells)))
                index += 1
            continue
        index += 1
    return out


def parse_frontmatter(text: str) -> dict:
    """`key: value` pairs from a YAML block, but only when the file OPENS with one.

    Deliberately anchored to the first line. `---` is also a markdown horizontal
    rule, and a scanner that toggles on every `---` reads prose as front matter.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}
    meta: dict = {}
    key = None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        match = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if match:
            key = match.group(1)
            value = match.group(2).strip()
            if value:
                meta[key] = value
        elif key and re.match(r"^\s*-\s+\S", line):
            meta[key] = (meta.get(key, "") + " " + line.strip()[1:].strip()).strip()
    return meta


def decision_lines(db, doc_id, want: int = 5) -> list[str]:
    """The prose of a record's Decision, following it into its subsections.

    Two shapes have to work. Most records put the decision directly under
    `## Decision`; some put an empty `## Decision` followed by `### The
    contract`, and a lookup on the heading alone reports those as having none.
    """
    rows = db.execute(
        "SELECT heading, line, body, level FROM section WHERE doc_id = ? ORDER BY line",
        (doc_id,),
    ).fetchall()
    start = next(
        (i for i, s in enumerate(rows) if s["heading"].strip().lower() == "decision"), None
    )
    if start is None:
        return []
    base = rows[start]["level"]
    out: list[str] = []
    for row_ in rows[start:]:
        if row_ is not rows[start] and row_["level"] <= base:
            break  # the next sibling heading ends the subtree
        fence = False
        for line in row_["body"].split("\n"):
            line = line.strip()
            if line.startswith("```"):
                fence = not fence
                continue
            # Prose only. A table row truncated to terminal width says nothing.
            if fence or not line or line.startswith(("|", ":--", "---")):
                continue
            out.append(line[:150])
            if len(out) >= want:
                return out
    return out


# --- CLI ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kb", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("build", help="(re)index every source")
    sub.add_parser("stale", help="which sources the index is behind")
    sub.add_parser("stats", help="what is indexed")
    for name, helptext in [
        ("search", "full text across every document"),
        ("why", "the decision, its evidence, and who cites it"),
    ]:
        node = sub.add_parser(name, help=helptext)
        node.add_argument("query", nargs="+")
    row_cmd = sub.add_parser("row", help="one table row with its headers")
    row_cmd.add_argument("key")
    row_cmd.add_argument("subject", nargs="?")
    section_cmd = sub.add_parser("section", help="one whole section")
    section_cmd.add_argument("subject")
    section_cmd.add_argument("heading", nargs="+")
    headings_cmd = sub.add_parser("headings", help="table of contents for a subject")
    headings_cmd.add_argument("subject")
    pack_cmd = sub.add_parser("pack", help="one bounded briefing")
    pack_cmd.add_argument("topic", nargs="+")
    pack_cmd.add_argument("--budget", type=int, default=2500)
    subjects_cmd = sub.add_parser("subjects", help="names section and headings accept")
    subjects_cmd.add_argument("filter", nargs="?")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    kb = Kb(Config.load())

    if args.command == "build":
        return kb.build()
    if args.command == "stale":
        added, changed, removed = kb.stale()
        for group, label in ((added, "added"), (changed, "changed"), (removed, "removed")):
            for path in group:
                print(f"  {label:<8} {path}")
        total = len(added) + len(changed) + len(removed)
        print(f"{total} file(s) differ from the index" if total else "index is current")
        print("KB OK")
        return 0

    kb.ready()
    if args.command == "search":
        return kb.search(" ".join(args.query))
    if args.command == "why":
        return kb.why(" ".join(args.query))
    if args.command == "row":
        return kb.row(args.key, args.subject)
    if args.command == "section":
        return kb.section(args.subject, " ".join(args.heading))
    if args.command == "headings":
        return kb.headings(args.subject)
    if args.command == "pack":
        return kb.pack(" ".join(args.topic), args.budget)
    if args.command == "subjects":
        return kb.subjects(args.filter)
    if args.command == "stats":
        return kb.stats()
    parser.print_help()
    return 2
